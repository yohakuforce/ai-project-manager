"""JSONL（1 行 1 イベント）形式の AuditLogRepository。

社内 PC 完結要件（security-governance-v1.md §6-1）に合わせ、
外部サービスに依存せずローカルファイルへ追記する。

設計方針:
  - 1 ログ = 1 JSON 行（ndjson 互換）
  - append-only。更新・削除はファイルローテーション以外で行わない。
  - 同期 I/O を asyncio.to_thread でラップ（aiofiles を依存追加しないため）。
  - 日付別ローテーション（`audit-YYYY-MM-DD.jsonl`）でファイル肥大を防ぐ。
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from src.domain.audit.aggregate import AuditAction, AuditLog, AuditLogId, TokenUsage
from src.domain.audit.repository import AuditLogRepository


def _serialize(log: AuditLog) -> dict:
    return {
        "audit_log_id": str(log.audit_log_id),
        "timestamp": log.timestamp.isoformat(),
        "actor": log.actor,
        "action": log.action.value,
        "project_id": log.project_id,
        "data_ref": log.data_ref,
        "llm_model": log.llm_model,
        "token_usage": (
            {
                "prompt_tokens": log.token_usage.prompt_tokens,
                "completion_tokens": log.token_usage.completion_tokens,
                "estimated_cost_usd": log.token_usage.estimated_cost_usd,
            }
            if log.token_usage
            else None
        ),
        "input_hash": log.input_hash,
    }


def _deserialize(data: dict) -> AuditLog:
    import uuid

    token_usage_data = data.get("token_usage")
    token_usage = (
        TokenUsage(
            prompt_tokens=int(token_usage_data["prompt_tokens"]),
            completion_tokens=int(token_usage_data["completion_tokens"]),
            estimated_cost_usd=float(token_usage_data["estimated_cost_usd"]),
        )
        if token_usage_data
        else None
    )
    return AuditLog(
        audit_log_id=AuditLogId(value=uuid.UUID(data["audit_log_id"])),
        timestamp=datetime.fromisoformat(data["timestamp"]),
        actor=data["actor"],
        action=AuditAction(data["action"]),
        project_id=data.get("project_id"),
        data_ref=data.get("data_ref"),
        llm_model=data.get("llm_model"),
        token_usage=token_usage,
        input_hash=data.get("input_hash"),
    )


class JsonlAuditLogRepository(AuditLogRepository):
    """JSONL ファイル（日付別）への append-only 監査ログ実装。"""

    def __init__(self, log_dir: Path | str) -> None:
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._write_lock = asyncio.Lock()

    async def append(self, log: AuditLog) -> None:
        path = self._path_for(log.timestamp)
        line = json.dumps(_serialize(log), ensure_ascii=False, separators=(",", ":"))
        async with self._write_lock:
            await asyncio.to_thread(self._append_line, path, line)

    async def find_by_project(self, project_id: str, limit: int = 100) -> list[AuditLog]:
        return await self._find_matching(
            predicate=lambda log: log.project_id == project_id,
            limit=limit,
        )

    async def find_by_actor(self, actor: str, limit: int = 100) -> list[AuditLog]:
        return await self._find_matching(
            predicate=lambda log: log.actor == actor,
            limit=limit,
        )

    def _path_for(self, ts: datetime) -> Path:
        ts_utc = ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)
        return self._log_dir / f"audit-{ts_utc.date().isoformat()}.jsonl"

    @staticmethod
    def _append_line(path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")

    async def _find_matching(self, *, predicate, limit: int) -> list[AuditLog]:
        all_logs = await asyncio.to_thread(self._read_all_logs)
        matching = [log for log in all_logs if predicate(log)]
        return matching[-limit:]

    def _read_all_logs(self) -> list[AuditLog]:
        logs: list[AuditLog] = []
        for path in sorted(self._log_dir.glob("audit-*.jsonl")):
            with path.open("r", encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        logs.append(_deserialize(json.loads(raw)))
                    except (ValueError, KeyError):
                        # 壊れた行は無視（運用上の不整合は別途モニタリング）
                        continue
        return logs

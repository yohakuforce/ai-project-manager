"""監査ログ infrastructure のユニットテスト。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.domain.audit.aggregate import AuditAction, AuditLog, TokenUsage
from src.infrastructure.audit.factory import build_audit_log_repository
from src.infrastructure.audit.in_memory import InMemoryAuditLogRepository
from src.infrastructure.audit.jsonl import JsonlAuditLogRepository


def _make_log(
    actor: str = "system",
    action: AuditAction = AuditAction.ALERT_CREATED,
    project_id: str | None = "p-001",
) -> AuditLog:
    return AuditLog.create(
        actor=actor,
        action=action,
        project_id=project_id,
        data_ref="ref-001",
    )


@pytest.mark.asyncio
class TestInMemoryAuditLogRepository:
    async def test_append_and_find_by_project(self) -> None:
        repo = InMemoryAuditLogRepository()
        await repo.append(_make_log(project_id="p-001"))
        await repo.append(_make_log(project_id="p-002"))

        p1_logs = await repo.find_by_project("p-001")
        assert len(p1_logs) == 1
        assert p1_logs[0].project_id == "p-001"

    async def test_find_by_actor_limit(self) -> None:
        repo = InMemoryAuditLogRepository()
        for _ in range(5):
            await repo.append(_make_log(actor="alice"))
        await repo.append(_make_log(actor="bob"))

        alice_logs = await repo.find_by_actor("alice", limit=3)
        assert len(alice_logs) == 3
        bob_logs = await repo.find_by_actor("bob")
        assert len(bob_logs) == 1


@pytest.mark.asyncio
class TestJsonlAuditLogRepository:
    async def test_append_writes_jsonl_line(self, tmp_path: Path) -> None:
        repo = JsonlAuditLogRepository(tmp_path)
        log = _make_log()
        await repo.append(log)

        log_files = sorted(tmp_path.glob("audit-*.jsonl"))
        assert len(log_files) == 1
        lines = log_files[0].read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["actor"] == log.actor
        assert parsed["action"] == log.action.value
        assert parsed["project_id"] == log.project_id

    async def test_round_trip_with_token_usage(self, tmp_path: Path) -> None:
        repo = JsonlAuditLogRepository(tmp_path)
        log = AuditLog.create(
            actor="ai-agent",
            action=AuditAction.LLM_CALL,
            project_id="p-001",
            llm_model="claude-haiku-4-5",
            token_usage=TokenUsage(
                prompt_tokens=100,
                completion_tokens=50,
                estimated_cost_usd=0.0003,
            ),
            input_hash="abc123",
        )
        await repo.append(log)

        found = await repo.find_by_project("p-001")
        assert len(found) == 1
        restored = found[0]
        assert restored.audit_log_id == log.audit_log_id
        assert restored.llm_model == "claude-haiku-4-5"
        assert restored.token_usage is not None
        assert restored.token_usage.prompt_tokens == 100
        assert restored.token_usage.total_tokens == 150
        assert restored.input_hash == "abc123"

    async def test_appends_to_date_partitioned_files(self, tmp_path: Path) -> None:
        repo = JsonlAuditLogRepository(tmp_path)
        today = AuditLog.create(actor="a", action=AuditAction.ALERT_CREATED)
        # 別日のログを作成
        other_day = AuditLog(
            audit_log_id=today.audit_log_id,
            timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            actor="a",
            action=AuditAction.ALERT_CREATED,
        )
        await repo.append(today)
        await repo.append(other_day)

        files = sorted(p.name for p in tmp_path.glob("audit-*.jsonl"))
        assert len(files) == 2
        assert any("2026-01-01" in name for name in files)

    async def test_find_by_actor_filters(self, tmp_path: Path) -> None:
        repo = JsonlAuditLogRepository(tmp_path)
        await repo.append(_make_log(actor="alice"))
        await repo.append(_make_log(actor="bob"))
        await repo.append(_make_log(actor="alice"))

        alice_logs = await repo.find_by_actor("alice")
        assert len(alice_logs) == 2
        assert all(log.actor == "alice" for log in alice_logs)


class TestBuildAuditLogRepository:
    def test_returns_in_memory_when_dir_empty(self) -> None:
        settings = Settings(audit_log_dir="")
        repo = build_audit_log_repository(settings)
        assert isinstance(repo, InMemoryAuditLogRepository)

    def test_returns_jsonl_when_dir_set(self, tmp_path: Path) -> None:
        settings = Settings(audit_log_dir=str(tmp_path))
        repo = build_audit_log_repository(settings)
        assert isinstance(repo, JsonlAuditLogRepository)

"""Audit Context。全操作の追記専用記録。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum


@dataclass(frozen=True)
class AuditLogId:
    value: uuid.UUID

    @classmethod
    def generate(cls) -> AuditLogId:
        return cls(value=uuid.uuid4())

    def __str__(self) -> str:
        return str(self.value)


class AuditAction(str, Enum):
    LLM_CALL = "llm_call"
    DATA_READ = "data_read"
    TASK_CREATED = "task_created"
    TASK_STATUS_CHANGED = "task_status_changed"
    ASSIGNMENT_CREATED = "assignment_created"
    ASSIGNMENT_CONFIRMED = "assignment_confirmed"
    ASSIGNMENT_REJECTED = "assignment_rejected"
    REPORT_DELIVERED = "report_delivered"
    REPORT_SUBMITTED = "report_submitted"
    ALERT_CREATED = "alert_created"
    ALERT_ACKNOWLEDGED = "alert_acknowledged"
    CONTEXT_HUB_QUERIED = "context_hub_queried"


@dataclass(frozen=True)
class TokenUsage:
    """LLM API 使用量の記録。"""

    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class AuditLog:
    """
    監査ログエンティティ。append-only。更新・削除禁止。
    security-governance-v1.md §6-1 監査ログ要件に準拠。
    """

    audit_log_id: AuditLogId
    timestamp: datetime
    actor: str  # ユーザー ID / "system" / "ai-agent"
    action: AuditAction
    project_id: str | None = None
    data_ref: str | None = None  # 操作対象の ID
    llm_model: str | None = None  # LLM 呼び出し時のみ
    token_usage: TokenUsage | None = None
    input_hash: str | None = None  # 入力データのハッシュ（生データは保存しない）

    @classmethod
    def create(
        cls,
        actor: str,
        action: AuditAction,
        project_id: str | None = None,
        data_ref: str | None = None,
        llm_model: str | None = None,
        token_usage: TokenUsage | None = None,
        input_hash: str | None = None,
    ) -> AuditLog:
        return cls(
            audit_log_id=AuditLogId.generate(),
            timestamp=datetime.now(UTC),
            actor=actor,
            action=action,
            project_id=project_id,
            data_ref=data_ref,
            llm_model=llm_model,
            token_usage=token_usage,
            input_hash=input_hash,
        )

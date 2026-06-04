"""Alert 集約ルート。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


@dataclass(frozen=True)
class AlertId:
    value: uuid.UUID

    @classmethod
    def generate(cls) -> AlertId:
        return cls(value=uuid.uuid4())

    def __str__(self) -> str:
        return str(self.value)


class AlertCategory(str, Enum):
    TASK_DELAY = "task_delay"
    MEMBER_OVERLOAD = "member_overload"
    CUSTOMER_NO_RESPONSE = "customer_no_response"
    PHASE_DEVIATION = "phase_deviation"
    PATTERN_DETECTED = "pattern_detected"


class AlertSeverity(str, Enum):
    CRITICAL = "critical"  # 即時通知
    HIGH = "high"  # 優先通知
    MEDIUM = "medium"  # 日次サマリにまとめて通知


class AlertStatus(str, Enum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class EvidenceType(str, Enum):
    REPORT_RESPONSE = "report_response"
    TASK_STATUS = "task_status"
    ISSUE_DATA = "issue_data"
    PATTERN = "pattern"


@dataclass(frozen=True)
class Evidence:
    """
    Alert の根拠データ。AI が "なぜアラートしたか" を必ず明示する。
    """

    evidence_type: EvidenceType
    data_ref: str  # 参照元 ID（taskId/reportId/etc）
    human_readable_summary: str  # "3日連続で進捗率が変化なし" 等の説明


@dataclass
class Alert:
    """
    Alert 集約ルート。
    AI が PL/PM へ提案する判断要請。人が acknowledge するまで ACTIVE。
    """

    alert_id: AlertId
    project_id: str  # ProjectId の文字列表現
    category: AlertCategory
    severity: AlertSeverity
    ai_generated_message: str  # AI が生成した通知メッセージ
    evidence: list[Evidence] = field(default_factory=list)
    target_task_id: str | None = None  # アラート対象のタスク（あれば）
    target_member_id: str | None = None  # アラート対象のメンバー（あれば）
    status: AlertStatus = AlertStatus.ACTIVE
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None

    def acknowledge(self, acknowledged_by: str) -> None:
        """PL/PM がアラートを確認する。"""
        self.status = AlertStatus.ACKNOWLEDGED
        self.acknowledged_by = acknowledged_by
        self.acknowledged_at = datetime.now(UTC)

    def resolve(self) -> None:
        """アラートを解決済みにする。"""
        self.status = AlertStatus.RESOLVED
        self.resolved_at = datetime.now(UTC)

    @property
    def requires_immediate_notification(self) -> bool:
        return self.severity == AlertSeverity.CRITICAL

"""
Project Management Context の値オブジェクト。
すべて immutable（frozen=True）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

# ============================================================
# 強型 ID（UUID ラッパー）
# ============================================================


@dataclass(frozen=True)
class ProjectId:
    value: uuid.UUID

    @classmethod
    def generate(cls) -> ProjectId:
        return cls(value=uuid.uuid4())

    @classmethod
    def from_str(cls, s: str) -> ProjectId:
        return cls(value=uuid.UUID(s))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class PhaseId:
    value: uuid.UUID

    @classmethod
    def generate(cls) -> PhaseId:
        return cls(value=uuid.uuid4())

    @classmethod
    def from_str(cls, s: str) -> PhaseId:
        return cls(value=uuid.UUID(s))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class MilestoneId:
    value: uuid.UUID

    @classmethod
    def generate(cls) -> MilestoneId:
        return cls(value=uuid.uuid4())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class TaskId:
    value: uuid.UUID

    @classmethod
    def generate(cls) -> TaskId:
        return cls(value=uuid.uuid4())

    @classmethod
    def from_str(cls, s: str) -> TaskId:
        return cls(value=uuid.UUID(s))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class AssignmentId:
    value: uuid.UUID

    @classmethod
    def generate(cls) -> AssignmentId:
        return cls(value=uuid.uuid4())

    def __str__(self) -> str:
        return str(self.value)


# ============================================================
# Enum
# ============================================================


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class TaskSource(str, Enum):
    MEETING_EXTRACTION = "meeting_extraction"
    ISSUE_IMPORT = "issue_import"
    MANUAL = "manual"


class AssignmentStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    CANCELLED = "cancelled"


# ============================================================
# 値オブジェクト
# ============================================================


@dataclass(frozen=True)
class ContextHubProjectRef:
    """Context-Hub 側のプロジェクトへの参照。"""

    context_hub_project_id: str  # UUID 文字列
    api_endpoint: str


@dataclass(frozen=True)
class Milestone:
    milestone_id: MilestoneId
    name: str
    due_date: date


@dataclass(frozen=True)
class Phase:
    phase_id: PhaseId
    name: str
    start_date: date
    planned_end_date: date
    completion_criteria: str
    milestones: tuple[Milestone, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class IssueStatusMapping:
    """
    Context-Hub 正規化 IssueStatus → AI-PM 内部 TaskStatus のマッピング。
    T-011 相互レビュー (3) の対応。
    """

    source_type: str  # "backlog" | "redmine"
    external_status_name: str  # Context-Hub 正規化後のステータス名
    internal_status: TaskStatus


@dataclass(frozen=True)
class PhaseProgress:
    """フェーズの計画対実績の状態。"""

    phase_id: PhaseId
    phase_name: str
    planned_end_date: date
    projected_end_date: date
    completion_rate: float  # 0.0〜1.0
    deviation_days: int  # 正値 = 遅延、負値 = 前倒し

    @property
    def is_delayed(self) -> bool:
        return self.deviation_days > 0

"""
Member Context の値オブジェクト。
T-011 相互レビュー (2): skills / availability は Context-Hub v1.0 スコープ外のため AI-PM 側で管理。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from enum import Enum


@dataclass(frozen=True)
class MemberId:
    value: uuid.UUID

    @classmethod
    def generate(cls) -> MemberId:
        return cls(value=uuid.uuid4())

    @classmethod
    def from_str(cls, s: str) -> MemberId:
        return cls(value=uuid.UUID(s))

    def __str__(self) -> str:
        return str(self.value)


class MemberRole(str, Enum):
    DEVELOPER = "developer"
    DESIGNER = "designer"
    PL = "pl"
    PM = "pm"
    QA = "qa"
    OTHER = "other"


class SkillCategory(str, Enum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    INFRA = "infra"
    QA = "qa"
    MANAGEMENT = "management"
    DESIGN = "design"
    OTHER = "other"


class SkillLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    EXPERT = "expert"


@dataclass(frozen=True)
class Skill:
    """メンバーのスキル値オブジェクト。AI-PM 側で自前管理。"""

    category: SkillCategory
    name: str
    level: SkillLevel
    years_of_experience: float


@dataclass(frozen=True)
class Availability:
    """特定日のメンバー稼働可否。AI-PM 側で自前管理。"""

    date: date
    available_hours: float  # 0.0〜8.0
    note: str | None = None

    def __post_init__(self) -> None:
        if not (0.0 <= self.available_hours <= 8.0):
            raise ValueError(f"available_hours must be 0.0-8.0, got {self.available_hours}")


@dataclass(frozen=True)
class PerformanceHistory:
    """メンバーの過去タスク完了記録。Assign の根拠データとして参照。"""

    task_id: str  # TaskId の文字列表現（集約間は ID 参照のみ）
    completed_at: date
    delay_days: int  # 0 = 期限内完了, 正値 = 遅延日数
    quality_note: str | None = None

    @property
    def was_on_time(self) -> bool:
        return self.delay_days <= 0

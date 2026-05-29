"""
Member 集約ルート。
Context-Hub v1.0 スコープ外の skills/availability/performanceHistory を AI-PM 側で管理する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from .value_objects import (
    Availability,
    MemberId,
    MemberRole,
    PerformanceHistory,
    Skill,
    SkillCategory,
)


@dataclass
class Member:
    """
    Member 集約ルート。
    externalId は Context-Hub GET /members の externalId と一致させる（照合キー）。
    """

    member_id: MemberId
    external_id: str  # Context-Hub GET /members の externalId
    name: str
    role: MemberRole
    skills: list[Skill] = field(default_factory=list)
    availability: list[Availability] = field(default_factory=list)
    performance_history: list[PerformanceHistory] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def add_skill(self, skill: Skill) -> None:
        """スキルを追加する。同一 category + name の重複は上書きする。"""
        self.skills = [
            s for s in self.skills if not (s.category == skill.category and s.name == skill.name)
        ]
        self.skills.append(skill)
        self.updated_at = datetime.now(UTC)

    def set_availability(self, availability: Availability) -> None:
        """特定日の稼働情報を設定する（既存のものは上書き）。"""
        self.availability = [a for a in self.availability if a.date != availability.date]
        self.availability.append(availability)
        self.updated_at = datetime.now(UTC)

    def add_performance_record(self, record: PerformanceHistory) -> None:
        """タスク完了記録を追加する。"""
        self.performance_history.append(record)
        self.updated_at = datetime.now(UTC)

    def available_hours_on(self, target_date: date) -> float:
        """特定日の稼働可能時間を返す。設定がない場合は 8.0 時間とみなす。"""
        for a in self.availability:
            if a.date == target_date:
                return a.available_hours
        return 8.0  # デフォルト: フル稼働

    def on_time_rate(self) -> float:
        """期限内完了率（0.0〜1.0）。履歴がない場合は 1.0 を返す。"""
        if not self.performance_history:
            return 1.0
        on_time_count = sum(1 for r in self.performance_history if r.was_on_time)
        return on_time_count / len(self.performance_history)

    def skills_in_category(self, category: SkillCategory) -> list[Skill]:
        return [s for s in self.skills if s.category == category]

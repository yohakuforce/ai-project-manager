"""Member 集約のユニットテスト。"""

from __future__ import annotations

from datetime import date

import pytest

from src.domain.member import (
    Availability,
    Member,
    MemberId,
    MemberRole,
    PerformanceHistory,
    Skill,
    SkillCategory,
    SkillLevel,
)


def make_member() -> Member:
    return Member(
        member_id=MemberId.generate(),
        external_id="member-001",
        name="テストメンバー",
        role=MemberRole.DEVELOPER,
    )


class TestMemberSkills:
    def test_add_skill_appends_to_skills(self) -> None:
        member = make_member()
        skill = Skill(
            category=SkillCategory.BACKEND,
            name="Python",
            level=SkillLevel.EXPERT,
            years_of_experience=3.0,
        )
        member.add_skill(skill)
        assert len(member.skills) == 1
        assert member.skills[0].name == "Python"

    def test_add_skill_overwrites_same_category_and_name(self) -> None:
        member = make_member()
        skill_v1 = Skill(SkillCategory.BACKEND, "Python", SkillLevel.INTERMEDIATE, 2.0)
        skill_v2 = Skill(SkillCategory.BACKEND, "Python", SkillLevel.EXPERT, 3.0)
        member.add_skill(skill_v1)
        member.add_skill(skill_v2)
        assert len(member.skills) == 1
        assert member.skills[0].level == SkillLevel.EXPERT

    def test_skills_in_category_filters_correctly(self) -> None:
        member = make_member()
        member.add_skill(Skill(SkillCategory.BACKEND, "Python", SkillLevel.EXPERT, 3.0))
        member.add_skill(Skill(SkillCategory.FRONTEND, "React", SkillLevel.INTERMEDIATE, 1.0))
        backend_skills = member.skills_in_category(SkillCategory.BACKEND)
        assert len(backend_skills) == 1
        assert backend_skills[0].name == "Python"


class TestMemberAvailability:
    def test_available_hours_on_returns_set_value(self) -> None:
        member = make_member()
        target_date = date(2026, 5, 20)
        member.set_availability(Availability(date=target_date, available_hours=4.0))
        assert member.available_hours_on(target_date) == 4.0

    def test_available_hours_on_returns_8_when_not_set(self) -> None:
        member = make_member()
        assert member.available_hours_on(date(2026, 5, 20)) == 8.0

    def test_set_availability_overwrites_existing_date(self) -> None:
        member = make_member()
        target_date = date(2026, 5, 20)
        member.set_availability(Availability(date=target_date, available_hours=4.0))
        member.set_availability(Availability(date=target_date, available_hours=0.0, note="休暇"))
        assert member.available_hours_on(target_date) == 0.0
        assert len(member.availability) == 1

    def test_availability_rejects_out_of_range_hours(self) -> None:
        with pytest.raises(ValueError):
            Availability(date=date(2026, 5, 20), available_hours=9.0)


class TestMemberPerformance:
    def test_on_time_rate_returns_1_when_no_history(self) -> None:
        member = make_member()
        assert member.on_time_rate() == 1.0

    def test_on_time_rate_calculates_correctly(self) -> None:
        member = make_member()
        member.add_performance_record(
            PerformanceHistory(task_id="t1", completed_at=date(2026, 5, 1), delay_days=0)
        )
        member.add_performance_record(
            PerformanceHistory(task_id="t2", completed_at=date(2026, 5, 5), delay_days=2)
        )
        member.add_performance_record(
            PerformanceHistory(task_id="t3", completed_at=date(2026, 5, 10), delay_days=0)
        )
        # 2/3 が期限内
        assert member.on_time_rate() == pytest.approx(2 / 3)

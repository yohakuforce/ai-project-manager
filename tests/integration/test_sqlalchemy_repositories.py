"""SqlAlchemy リポジトリ実装の統合テスト。

aiosqlite（インメモリ）でテーブルを建てて、各リポジトリの round-trip
（save → find_by_id で同等の集約が復元される）を検証する。

JSONB カラムは models 側で ``JSON().with_variant(JSONB, "postgresql")`` で
ディスパッチしているため、SQLite では通常の JSON 型として動作する。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.alert.aggregate import (
    Alert,
    AlertCategory,
    AlertId,
    AlertSeverity,
    AlertStatus,
    Evidence,
    EvidenceType,
)
from src.domain.gate.aggregate import (
    GateDecision,
    GateStatus,
    GateType,
    LeaderGate,
)
from src.domain.member.aggregate import Member
from src.domain.member.value_objects import (
    Availability,
    MemberId,
    MemberRole,
    Skill,
    SkillCategory,
    SkillLevel,
)
from src.domain.project.aggregate import Project
from src.domain.project.entities import Assignment, Task
from src.domain.project.value_objects import (
    AssignmentId,
    AssignmentStatus,
    ContextHubProjectRef,
    IssueStatusMapping,
    ProjectId,
    ProjectStatus,
    TaskId,
    TaskPriority,
    TaskSource,
    TaskStatus,
)
from src.domain.reporting.aggregate import DailyReport
from src.domain.reporting.value_objects import (
    DailyReportId,
    QuestionId,
    QuestionType,
    ReportQuestion,
    ReportStatus,
    ReportTemplate,
)
from src.infrastructure.db.base import Base
from src.infrastructure.repositories.sqlalchemy import (
    SqlAlchemyAlertRepository,
    SqlAlchemyDailyReportRepository,
    SqlAlchemyLeaderGateRepository,
    SqlAlchemyMemberRepository,
    SqlAlchemyProjectRepository,
)


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


# ============================================================
# Project Repository
# ============================================================


def _make_project_with_children() -> Project:
    project = Project(
        project_id=ProjectId.generate(),
        name="テストPJ",
        customer="顧客A",
        goal="目標",
        context_hub_ref=ContextHubProjectRef(
            context_hub_project_id="hub-001",
            api_endpoint="http://localhost:8000",
        ),
        issue_status_mappings=[
            IssueStatusMapping(
                source_type="backlog",
                external_status_name="処理中",
                internal_status=TaskStatus.IN_PROGRESS,
            ),
        ],
    )
    task = Task(
        task_id=TaskId.generate(),
        title="設計",
        description="DDD で設計",
        status=TaskStatus.IN_PROGRESS,
        priority=TaskPriority.HIGH,
        source=TaskSource.MEETING_EXTRACTION,
        source_ref="meeting-1",
        due_date=date(2026, 5, 31),
        estimated_hours=8.0,
        ai_confidence=0.9,
    )
    project.tasks.append(task)
    project.assignments.append(
        Assignment(
            assignment_id=AssignmentId.generate(),
            task_id=task.task_id,
            member_id="member-001",
            status=AssignmentStatus.DRAFT,
            ai_rationale="スキル一致",
        )
    )
    return project


@pytest.mark.asyncio
class TestSqlAlchemyProjectRepository:
    async def test_save_and_find_by_id_round_trip(self, session_factory) -> None:
        repo = SqlAlchemyProjectRepository(session_factory)
        original = _make_project_with_children()

        await repo.save(original)
        found = await repo.find_by_id(original.project_id)

        assert found is not None
        assert found.name == original.name
        assert found.customer == original.customer
        assert len(found.tasks) == 1
        assert found.tasks[0].title == "設計"
        assert found.tasks[0].priority == TaskPriority.HIGH
        assert len(found.assignments) == 1
        assert found.assignments[0].member_id == "member-001"
        assert len(found.issue_status_mappings) == 1
        assert found.issue_status_mappings[0].internal_status == TaskStatus.IN_PROGRESS

    async def test_save_updates_existing_aggregate(self, session_factory) -> None:
        repo = SqlAlchemyProjectRepository(session_factory)
        project = _make_project_with_children()
        await repo.save(project)

        # 子要素の差し替え
        new_task = Task(
            task_id=TaskId.generate(),
            title="実装",
            description="",
            status=TaskStatus.PENDING,
            priority=TaskPriority.NORMAL,
            source=TaskSource.MANUAL,
        )
        project.tasks.append(new_task)
        project.customer = "顧客B"

        await repo.save(project)
        found = await repo.find_by_id(project.project_id)

        assert found is not None
        assert found.customer == "顧客B"
        assert len(found.tasks) == 2
        titles = {t.title for t in found.tasks}
        assert {"設計", "実装"} == titles

    async def test_find_all_active_filters_by_status(self, session_factory) -> None:
        repo = SqlAlchemyProjectRepository(session_factory)
        active = _make_project_with_children()
        on_hold = _make_project_with_children()
        on_hold.status = ProjectStatus.ON_HOLD
        await repo.save(active)
        await repo.save(on_hold)

        actives = await repo.find_all_active()

        assert len(actives) == 1
        assert actives[0].project_id == active.project_id

    async def test_find_by_id_returns_none_when_missing(self, session_factory) -> None:
        repo = SqlAlchemyProjectRepository(session_factory)
        found = await repo.find_by_id(ProjectId.generate())
        assert found is None


# ============================================================
# Member Repository
# ============================================================


def _make_member() -> Member:
    return Member(
        member_id=MemberId.generate(),
        external_id="ext-001",
        name="田中 太郎",
        role=MemberRole.DEVELOPER,
        skills=[
            Skill(
                category=SkillCategory.BACKEND,
                name="Python",
                level=SkillLevel.EXPERT,
                years_of_experience=5.0,
            )
        ],
        availability=[
            Availability(date=date(2026, 5, 20), available_hours=6.0, note="午後のみ"),
        ],
    )


@pytest.mark.asyncio
class TestSqlAlchemyMemberRepository:
    async def test_save_and_find_round_trip(self, session_factory) -> None:
        repo = SqlAlchemyMemberRepository(session_factory)
        original = _make_member()

        await repo.save(original)
        found = await repo.find_by_id(original.member_id)

        assert found is not None
        assert found.name == "田中 太郎"
        assert found.role == MemberRole.DEVELOPER
        assert len(found.skills) == 1
        assert found.skills[0].name == "Python"
        assert found.skills[0].level == SkillLevel.EXPERT
        assert len(found.availability) == 1
        assert found.availability[0].note == "午後のみ"

    async def test_find_by_external_id(self, session_factory) -> None:
        repo = SqlAlchemyMemberRepository(session_factory)
        member = _make_member()
        await repo.save(member)

        found = await repo.find_by_external_id("ext-001")
        assert found is not None
        assert found.member_id == member.member_id

    async def test_save_updates_existing(self, session_factory) -> None:
        repo = SqlAlchemyMemberRepository(session_factory)
        member = _make_member()
        await repo.save(member)

        member.name = "田中 次郎"
        await repo.save(member)

        found = await repo.find_by_id(member.member_id)
        assert found is not None
        assert found.name == "田中 次郎"

    async def test_find_all_returns_all_members(self, session_factory) -> None:
        repo = SqlAlchemyMemberRepository(session_factory)
        m1 = _make_member()
        m2 = Member(
            member_id=MemberId.generate(),
            external_id="ext-002",
            name="鈴木",
            role=MemberRole.PM,
        )
        await repo.save(m1)
        await repo.save(m2)

        all_members = await repo.find_all()
        assert {m.external_id for m in all_members} == {"ext-001", "ext-002"}


# ============================================================
# Alert Repository
# ============================================================


def _make_alert(project_id: str = "project-001") -> Alert:
    return Alert(
        alert_id=AlertId.generate(),
        project_id=project_id,
        category=AlertCategory.TASK_DELAY,
        severity=AlertSeverity.HIGH,
        ai_generated_message="タスクが遅延しています",
        evidence=[
            Evidence(
                evidence_type=EvidenceType.TASK_STATUS,
                data_ref="task-001",
                human_readable_summary="期日3日超過",
            ),
        ],
        target_task_id="task-001",
    )


@pytest.mark.asyncio
class TestSqlAlchemyAlertRepository:
    async def test_save_and_find_round_trip(self, session_factory) -> None:
        repo = SqlAlchemyAlertRepository(session_factory)
        alert = _make_alert()
        await repo.save(alert)

        found = await repo.find_by_id(alert.alert_id)
        assert found is not None
        assert found.severity == AlertSeverity.HIGH
        assert len(found.evidence) == 1
        assert found.evidence[0].data_ref == "task-001"

    async def test_find_active_by_project_excludes_resolved(self, session_factory) -> None:
        repo = SqlAlchemyAlertRepository(session_factory)
        active = _make_alert("p1")
        resolved = _make_alert("p1")
        resolved.status = AlertStatus.RESOLVED
        resolved.resolved_at = datetime.now(UTC)
        await repo.save(active)
        await repo.save(resolved)

        actives = await repo.find_active_by_project("p1")
        assert len(actives) == 1
        assert actives[0].alert_id == active.alert_id

    async def test_acknowledge_persists(self, session_factory) -> None:
        repo = SqlAlchemyAlertRepository(session_factory)
        alert = _make_alert()
        await repo.save(alert)

        alert.acknowledge("pl-user")
        await repo.save(alert)

        found = await repo.find_by_id(alert.alert_id)
        assert found is not None
        assert found.status == AlertStatus.ACKNOWLEDGED
        assert found.acknowledged_by == "pl-user"


# ============================================================
# DailyReport Repository
# ============================================================


def _make_daily_report(member_id: str = "member-001") -> DailyReport:
    questions = [
        ReportQuestion(
            question_id=QuestionId.generate(),
            question_type=QuestionType.PROGRESS_PERCENT,
            body="進捗率は？",
        ),
        ReportQuestion(
            question_id=QuestionId.generate(),
            question_type=QuestionType.BLOCKER,
            body="ブロッカーは？",
        ),
    ]
    return DailyReport(
        report_id=DailyReportId.generate(),
        member_id=member_id,
        project_id="project-001",
        report_date=date(2026, 5, 18),
        template=ReportTemplate.create(questions),
    )


@pytest.mark.asyncio
class TestSqlAlchemyDailyReportRepository:
    async def test_save_and_find_round_trip(self, session_factory) -> None:
        repo = SqlAlchemyDailyReportRepository(session_factory)
        report = _make_daily_report()
        await repo.save(report)

        found = await repo.find_by_id(report.report_id)
        assert found is not None
        assert len(found.template.questions) == 2
        assert found.status == ReportStatus.PENDING

    async def test_find_by_member_and_date(self, session_factory) -> None:
        repo = SqlAlchemyDailyReportRepository(session_factory)
        report = _make_daily_report()
        await repo.save(report)

        found = await repo.find_by_member_and_date("member-001", date(2026, 5, 18))
        assert found is not None
        assert found.report_id == report.report_id

        miss = await repo.find_by_member_and_date(
            "member-001", date(2026, 5, 18) + timedelta(days=1)
        )
        assert miss is None

    async def test_submit_responses_persists(self, session_factory) -> None:
        repo = SqlAlchemyDailyReportRepository(session_factory)
        report = _make_daily_report()
        await repo.save(report)

        q0 = report.template.questions[0]
        report.submit_response(q0.question_id, "80")
        report.mark_delivered()
        await repo.save(report)

        found = await repo.find_by_id(report.report_id)
        assert found is not None
        assert found.status == ReportStatus.DELIVERED
        assert len(found.responses) == 1
        assert found.responses[0].response_text == "80"

    async def test_find_by_status(self, session_factory) -> None:
        repo = SqlAlchemyDailyReportRepository(session_factory)
        pending = _make_daily_report("m1")
        delivered = _make_daily_report("m2")
        delivered.mark_delivered()
        await repo.save(pending)
        await repo.save(delivered)

        results = await repo.find_by_status("project-001", ReportStatus.DELIVERED)
        assert len(results) == 1
        assert results[0].member_id == "m2"


# ============================================================
# LeaderGate Repository
# ============================================================


def _make_gate(project_id: str = "project-001") -> LeaderGate:
    return LeaderGate.create(
        project_id=project_id,
        gate_type=GateType.TASK_STATE_CURRENT,
        gate_date=date(2026, 6, 2),
        context={"notable_task_ids": ["t1", "t2"]},
    )


class TestSqlAlchemyLeaderGateRepository:
    async def test_save_and_find_by_id_round_trip(self, session_factory) -> None:
        repo = SqlAlchemyLeaderGateRepository(session_factory)
        gate = _make_gate()
        await repo.save(gate)

        found = await repo.find_by_id(gate.gate_id)
        assert found is not None
        assert found.gate_type == GateType.TASK_STATE_CURRENT
        assert found.status == GateStatus.PENDING
        assert found.context["notable_task_ids"] == ["t1", "t2"]
        assert found.gate_date == date(2026, 6, 2)

    async def test_find_pending_excludes_resolved(self, session_factory) -> None:
        repo = SqlAlchemyLeaderGateRepository(session_factory)
        pending = _make_gate()
        resolved = _make_gate()
        resolved.resolve(decision=GateDecision.PROCEED, resolved_by="leader-1")
        await repo.save(pending)
        await repo.save(resolved)

        results = await repo.find_pending_by_project("project-001")
        assert len(results) == 1
        assert results[0].gate_id == pending.gate_id

    async def test_resolve_persists_across_reload(self, session_factory) -> None:
        # 翌日の確認を想定: 保存 → 別インスタンスから取得して解決 → 再取得で確定が残る
        repo = SqlAlchemyLeaderGateRepository(session_factory)
        gate = _make_gate()
        await repo.save(gate)

        reloaded = await repo.find_by_id(gate.gate_id)
        reloaded.resolve(decision=GateDecision.PROCEED, resolved_by="leader-1")
        await repo.save(reloaded)

        again = await repo.find_by_id(gate.gate_id)
        assert again.status == GateStatus.RESOLVED
        assert again.decision == GateDecision.PROCEED
        assert again.resolved_by == "leader-1"
        assert again.resolved_at is not None

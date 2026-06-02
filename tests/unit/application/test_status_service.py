"""ProjectStatusService.run_final_analysis のユニットテスト。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.application.assign.service import AssignService
from src.application.overview.service import OverviewService
from src.application.status.service import ProjectStatusService
from src.domain.member.aggregate import Member
from src.domain.member.value_objects import MemberId, MemberRole
from src.domain.project.aggregate import Project
from src.domain.project.entities import Task
from src.domain.project.value_objects import (
    ContextHubProjectRef,
    ProjectId,
    TaskId,
    TaskPriority,
    TaskSource,
    TaskStatus,
)
from src.infrastructure.llm.mock_adapter import MockLLMAdapter
from src.infrastructure.notifiers import InMemoryNotifier
from src.infrastructure.repositories.in_memory import (
    InMemoryAlertRepository,
    InMemoryDailyReportRepository,
    InMemoryMemberRepository,
    InMemoryProjectRepository,
)

pytestmark = pytest.mark.asyncio

ANALYSIS_DATE = date(2026, 6, 2)


async def _build(*, overdue: bool):
    project_repo = InMemoryProjectRepository()
    member_repo = InMemoryMemberRepository()
    report_repo = InMemoryDailyReportRepository()
    alert_repo = InMemoryAlertRepository()
    notifier = InMemoryNotifier()
    llm = MockLLMAdapter(fixed_response="全体は概ね順調です。")

    project = Project(
        project_id=ProjectId.generate(),
        name="PJ",
        customer="顧客",
        goal="目標",
        context_hub_ref=ContextHubProjectRef(context_hub_project_id="hub", api_endpoint="http://x"),
    )
    # 未割当タスク（DRAFT アサインの対象になる）
    project.tasks.append(
        Task(
            task_id=TaskId.generate(),
            title="未割当タスク",
            description="",
            status=TaskStatus.PENDING,
            priority=TaskPriority.NORMAL,
            source=TaskSource.MANUAL,
            due_date=(date.today() - timedelta(days=1)) if overdue else None,
        )
    )
    await project_repo.save(project)
    member = Member(
        member_id=MemberId.generate(),
        external_id="ext-1",
        name="担当",
        role=MemberRole.DEVELOPER,
    )
    await member_repo.save(member)

    overview = OverviewService(
        project_repository=project_repo,
        member_repository=member_repo,
        alert_repository=alert_repo,
        daily_report_repository=report_repo,
        llm_adapter=llm,
    )
    assign = AssignService(
        project_repository=project_repo,
        member_repository=member_repo,
        llm_adapter=llm,
    )
    service = ProjectStatusService(
        overview_service=overview,
        assign_service=assign,
        notifier=notifier,
        leader_channel="#leader",
    )
    return service, notifier, str(project.project_id)


class TestFinalAnalysis:
    async def test_creates_drafts_and_reports_to_leader(self) -> None:
        service, notifier, pid = await _build(overdue=False)
        result = await service.run_final_analysis(pid, ANALYSIS_DATE)

        assert result.drafts_created == 1
        assert result.health == "healthy"
        assert result.leader_notified is True
        msg = notifier.filter("message")[0]
        assert msg.payload.kind == "status"
        assert msg.channel == "#leader"

    async def test_overdue_task_marks_attention(self) -> None:
        service, _, pid = await _build(overdue=True)
        result = await service.run_final_analysis(pid, ANALYSIS_DATE)
        assert result.health == "attention"

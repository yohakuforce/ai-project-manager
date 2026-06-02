"""WrapUpService のユニットテスト — 提出状況による分岐とゲート起票。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.application.gate.service import GateService
from src.application.overview.service import OverviewService
from src.application.wrapup.service import WrapUpService
from src.domain.gate.aggregate import GateType
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
from src.domain.reporting.aggregate import DailyReport
from src.domain.reporting.value_objects import (
    DailyReportId,
    QuestionId,
    QuestionType,
    ReportQuestion,
    ReportStatus,
    ReportTemplate,
)
from src.infrastructure.llm.mock_adapter import MockLLMAdapter
from src.infrastructure.notifiers import InMemoryNotifier
from src.infrastructure.repositories.in_memory import (
    InMemoryAlertRepository,
    InMemoryDailyReportRepository,
    InMemoryLeaderGateRepository,
    InMemoryMemberRepository,
    InMemoryProjectRepository,
)

pytestmark = pytest.mark.asyncio

WRAP_DATE = date(2026, 6, 2)


def _report(member_id: str, status: ReportStatus, project_id: str) -> DailyReport:
    return DailyReport(
        report_id=DailyReportId.generate(),
        member_id=member_id,
        project_id=project_id,
        report_date=WRAP_DATE,
        template=ReportTemplate.create(
            [
                ReportQuestion(
                    question_id=QuestionId.generate(),
                    question_type=QuestionType.FREE_TEXT,
                    body="本日は？",
                )
            ]
        ),
        status=status,
    )


async def _build():
    project_repo = InMemoryProjectRepository()
    member_repo = InMemoryMemberRepository()
    report_repo = InMemoryDailyReportRepository()
    alert_repo = InMemoryAlertRepository()
    gate_repo = InMemoryLeaderGateRepository()
    notifier = InMemoryNotifier()
    llm = MockLLMAdapter(fixed_response="本日の総括です。")

    project = Project(
        project_id=ProjectId.generate(),
        name="PJ",
        customer="顧客",
        goal="目標",
        context_hub_ref=ContextHubProjectRef(context_hub_project_id="hub", api_endpoint="http://x"),
    )
    project.tasks.append(
        Task(
            task_id=TaskId.generate(),
            title="遅延タスク",
            description="",
            status=TaskStatus.IN_PROGRESS,
            priority=TaskPriority.HIGH,
            source=TaskSource.MANUAL,
            due_date=date.today() - timedelta(days=1),
        )
    )
    await project_repo.save(project)

    overview = OverviewService(
        project_repository=project_repo,
        member_repository=member_repo,
        alert_repository=alert_repo,
        daily_report_repository=report_repo,
        llm_adapter=llm,
    )
    gate_service = GateService(gate_repo)
    wrapup = WrapUpService(
        project_repository=project_repo,
        member_repository=member_repo,
        daily_report_repository=report_repo,
        overview_service=overview,
        gate_service=gate_service,
        notifier=notifier,
        leader_channel="#leader",
    )
    return wrapup, gate_service, member_repo, report_repo, notifier, str(project.project_id)


def _member(name: str) -> Member:
    return Member(
        member_id=MemberId.generate(),
        external_id=f"ext-{name}",
        name=name,
        role=MemberRole.DEVELOPER,
    )


class TestWrapUpRun:
    async def test_all_submitted_opens_task_state_gate(self) -> None:
        wrapup, gate_service, member_repo, report_repo, notifier, pid = await _build()
        m = _member("提出済")
        await member_repo.save(m)
        await report_repo.save(_report(str(m.member_id), ReportStatus.SUBMITTED, pid))

        result = await wrapup.run(pid, WRAP_DATE)

        assert result.all_submitted is True
        assert result.gate_type_opened == GateType.TASK_STATE_CURRENT.value
        pending = await gate_service.list_pending(pid)
        assert len(pending) == 1
        assert pending[0].gate_type == "task_state_current"
        assert notifier.filter("message")[0].payload.kind == "gate"

    async def test_unsubmitted_opens_wrap_up_decision_gate(self) -> None:
        wrapup, gate_service, member_repo, report_repo, notifier, pid = await _build()
        m = _member("未提出")
        await member_repo.save(m)
        await report_repo.save(_report(str(m.member_id), ReportStatus.DELIVERED, pid))

        result = await wrapup.run(pid, WRAP_DATE)

        assert result.all_submitted is False
        assert result.gate_type_opened == GateType.WRAP_UP_DECISION.value
        assert result.unsubmitted_member_ids == [str(m.member_id)]
        pending = await gate_service.list_pending(pid)
        assert pending[0].gate_type == "wrap_up_decision"
        # リーダーへ「総括しますか」打診
        assert notifier.filter("message")[0].payload.kind == "gate"

    async def test_run_summary_and_open_gate_directly(self) -> None:
        # WRAP_UP_DECISION を PROCEED 解決した後に呼ばれる継続処理に相当
        wrapup, gate_service, _member_repo, _report_repo, _notifier, pid = await _build()
        result = await wrapup.run_summary_and_open_gate(pid, WRAP_DATE)
        assert result.gate_type_opened == GateType.TASK_STATE_CURRENT.value
        assert len(await gate_service.list_pending(pid)) == 1

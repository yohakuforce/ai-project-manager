"""StandupService のユニットテスト。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.application.assign.service import AssignService
from src.application.standup.service import StandupService
from src.domain.member.aggregate import Member
from src.domain.member.value_objects import MemberId, MemberRole
from src.domain.project.aggregate import Project
from src.domain.project.entities import Assignment, Task
from src.domain.project.value_objects import (
    AssignmentId,
    AssignmentStatus,
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
    ReportResponse,
    ReportStatus,
    ReportTemplate,
)
from src.infrastructure.llm.mock_adapter import MockLLMAdapter
from src.infrastructure.notifiers import InMemoryNotifier
from src.infrastructure.repositories.in_memory import (
    InMemoryDailyReportRepository,
    InMemoryMemberRepository,
    InMemoryProjectMemberRepository,
    InMemoryProjectRepository,
)

pytestmark = pytest.mark.asyncio

TODAY = date(2026, 6, 2)
YESTERDAY = TODAY - timedelta(days=1)


def _member(name: str) -> Member:
    return Member(
        member_id=MemberId.generate(),
        external_id=f"ext-{name}",
        name=name,
        role=MemberRole.DEVELOPER,
    )


def _report_with_blocker(member_id: str, project_id: str) -> DailyReport:
    blocker_q = ReportQuestion(
        question_id=QuestionId.generate(),
        question_type=QuestionType.BLOCKER,
        body="ブロッカーは？",
    )
    template = ReportTemplate.create([blocker_q])
    return DailyReport(
        report_id=DailyReportId.generate(),
        member_id=member_id,
        project_id=project_id,
        report_date=YESTERDAY,
        template=template,
        responses=[
            ReportResponse.create(
                question_id=blocker_q.question_id, response_text="API 仕様待ちで停滞"
            )
        ],
        status=ReportStatus.SUBMITTED,
    )


async def _build():
    project_repo = InMemoryProjectRepository()
    member_repo = InMemoryMemberRepository()
    report_repo = InMemoryDailyReportRepository()
    notifier = InMemoryNotifier()
    llm = MockLLMAdapter(fixed_response="スタンドアップ要約。")

    project = Project(
        project_id=ProjectId.generate(),
        name="PJ",
        customer="顧客",
        goal="目標",
        context_hub_ref=ContextHubProjectRef(context_hub_project_id="hub", api_endpoint="http://x"),
    )
    overdue_task = Task(
        task_id=TaskId.generate(),
        title="遅延タスク",
        description="",
        status=TaskStatus.IN_PROGRESS,
        priority=TaskPriority.HIGH,
        source=TaskSource.MANUAL,
        due_date=date.today() - timedelta(days=2),
    )
    project.tasks.append(overdue_task)
    await project_repo.save(project)

    current = _member("現担当")
    candidate = _member("代替")
    await member_repo.save(current)
    await member_repo.save(candidate)
    # プロジェクトにメンバーを所属させる
    pm_repo = InMemoryProjectMemberRepository(member_repo)
    await pm_repo.add(project.project_id, current.member_id)
    await pm_repo.add(project.project_id, candidate.member_id)
    # overdue_task を current に confirmed 割当（=アサイン問題の対象）
    project.assignments.append(
        Assignment(
            assignment_id=AssignmentId.generate(),
            task_id=overdue_task.task_id,
            member_id=str(current.member_id),
            status=AssignmentStatus.CONFIRMED,
            ai_rationale="初期割当",
        )
    )
    await project_repo.save(project)
    await report_repo.save(_report_with_blocker(str(current.member_id), str(project.project_id)))

    assign = AssignService(
        project_repository=project_repo,
        member_repository=member_repo,
        llm_adapter=llm,
    )
    service = StandupService(
        project_repository=project_repo,
        member_repository=member_repo,
        daily_report_repository=report_repo,
        assign_service=assign,
        llm_adapter=llm,
        context_hub_client=None,
        notifier=notifier,
        leader_channel="#leader",
    )
    return service, project_repo, notifier, str(project.project_id), str(overdue_task.task_id)


class TestStandup:
    async def test_generates_standup_and_proposes_reassignment(self) -> None:
        service, project_repo, notifier, pid, task_id = await _build()

        result = await service.run(pid, TODAY)

        # 昨日のブロッカーを拾う
        assert result.yesterday_submitted == 1
        assert any("API 仕様" in b for b in result.blockers)
        # 期日超過の確認済みタスクが問題として検知される
        assert task_id in result.problem_task_ids
        # 別メンバーへの DRAFT 入替案が生成される
        assert result.reassignments_created == 1
        saved = await project_repo.find_by_id(ProjectId.from_str(pid))
        assert len(saved.draft_assignments()) == 1
        # リーダーへスタンドアップ共有
        assert result.leader_notified is True
        assert notifier.filter("message")[0].payload.kind == "standup"

    async def test_no_problems_still_notifies(self) -> None:
        # 問題タスクなし・日報なしでもスタンドアップは出る
        project_repo = InMemoryProjectRepository()
        member_repo = InMemoryMemberRepository()
        report_repo = InMemoryDailyReportRepository()
        notifier = InMemoryNotifier()
        llm = MockLLMAdapter(fixed_response="平穏。")
        project = Project(
            project_id=ProjectId.generate(),
            name="PJ",
            customer="c",
            goal="g",
            context_hub_ref=ContextHubProjectRef(
                context_hub_project_id="hub", api_endpoint="http://x"
            ),
        )
        await project_repo.save(project)
        assign = AssignService(
            project_repository=project_repo,
            member_repository=member_repo,
            llm_adapter=llm,
        )
        service = StandupService(
            project_repository=project_repo,
            member_repository=member_repo,
            daily_report_repository=report_repo,
            assign_service=assign,
            llm_adapter=llm,
            notifier=notifier,
            leader_channel="#leader",
        )
        result = await service.run(str(project.project_id), TODAY)
        assert result.problem_task_ids == []
        assert result.reassignments_created == 0
        assert result.leader_notified is True

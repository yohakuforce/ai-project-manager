"""
OverviewService のユニットテスト。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.application.overview.service import OverviewService
from src.domain.member.aggregate import Member
from src.domain.member.value_objects import MemberId, MemberRole
from src.domain.project.aggregate import Project
from src.domain.project.entities import Task
from src.domain.project.value_objects import (
    ContextHubProjectRef,
    Phase,
    PhaseId,
    ProjectId,
    TaskId,
    TaskPriority,
    TaskSource,
    TaskStatus,
)
from src.infrastructure.llm.mock_adapter import MockLLMAdapter
from src.infrastructure.repositories.in_memory import (
    InMemoryAlertRepository,
    InMemoryDailyReportRepository,
    InMemoryMemberRepository,
    InMemoryProjectRepository,
)


def _make_project_with_tasks() -> Project:
    project = Project(
        project_id=ProjectId.generate(),
        name="テストPJ",
        customer="顧客",
        goal="目標",
        context_hub_ref=ContextHubProjectRef(
            context_hub_project_id="hub-001",
            api_endpoint="http://localhost:8000",
        ),
    )
    # PENDING タスク
    project.tasks.append(
        Task(
            task_id=TaskId.generate(),
            title="タスク1",
            description="",
            status=TaskStatus.PENDING,
            priority=TaskPriority.NORMAL,
            source=TaskSource.MANUAL,
        )
    )
    # DONE タスク
    project.tasks.append(
        Task(
            task_id=TaskId.generate(),
            title="タスク2",
            description="",
            status=TaskStatus.DONE,
            priority=TaskPriority.NORMAL,
            source=TaskSource.MANUAL,
        )
    )
    # 遅延タスク
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
    return project


@pytest.fixture
def project_repo():
    return InMemoryProjectRepository()


@pytest.fixture
def member_repo():
    return InMemoryMemberRepository()


@pytest.fixture
def alert_repo():
    return InMemoryAlertRepository()


@pytest.fixture
def report_repo():
    return InMemoryDailyReportRepository()


@pytest.fixture
def llm():
    return MockLLMAdapter(fixed_response="本日の進捗サマリです。")


@pytest.fixture
def service(project_repo, member_repo, alert_repo, report_repo, llm):
    return OverviewService(
        project_repository=project_repo,
        member_repository=member_repo,
        alert_repository=alert_repo,
        daily_report_repository=report_repo,
        llm_adapter=llm,
    )


@pytest.mark.asyncio
class TestGenerateDailySummary:
    async def test_returns_correct_task_counts(self, service, project_repo) -> None:
        project = _make_project_with_tasks()
        await project_repo.save(project)

        result = await service.generate_daily_summary(project_id=str(project.project_id))

        assert result.task_summary.total == 3
        assert result.task_summary.done == 1
        assert result.task_summary.pending == 1
        assert result.task_summary.overdue == 1

    async def test_includes_ai_narrative(self, service, project_repo) -> None:
        project = _make_project_with_tasks()
        await project_repo.save(project)

        result = await service.generate_daily_summary(project_id=str(project.project_id))

        assert result.ai_narrative != ""

    async def test_raises_for_unknown_project(self, service) -> None:
        with pytest.raises(ValueError, match="Project が見つかりません"):
            await service.generate_daily_summary(project_id=str(ProjectId.generate()))

    async def test_report_summary_reflects_member_count(
        self, service, project_repo, member_repo
    ) -> None:
        project = _make_project_with_tasks()
        member = Member(
            member_id=MemberId.generate(),
            external_id="ext-001",
            name="田中",
            role=MemberRole.DEVELOPER,
        )
        await project_repo.save(project)
        await member_repo.save(member)

        result = await service.generate_daily_summary(project_id=str(project.project_id))

        assert result.report_summary.total_members == 1


@pytest.mark.asyncio
class TestGeneratePhaseProgress:
    async def test_returns_empty_phases_for_project_without_phases(
        self, service, project_repo
    ) -> None:
        project = _make_project_with_tasks()  # フェーズなし
        await project_repo.save(project)

        result = await service.generate_phase_progress(project_id=str(project.project_id))

        assert result.phases == []
        assert result.overall_completion_rate == 0.0

    async def test_calculates_completion_rate_correctly(self, service, project_repo) -> None:
        project = Project(
            project_id=ProjectId.generate(),
            name="フェーズPJ",
            customer="顧客",
            goal="目標",
            context_hub_ref=ContextHubProjectRef(
                context_hub_project_id="hub-003",
                api_endpoint="http://localhost:8000",
            ),
        )
        # フェーズ追加
        phase = Phase(
            phase_id=PhaseId.generate(),
            name="開発フェーズ",
            start_date=date.today() - timedelta(days=30),
            planned_end_date=date.today() + timedelta(days=30),
            completion_criteria="コード完成",
        )
        project.phases.append(phase)

        # タスク: 1 DONE / 1 PENDING
        project.tasks.append(
            Task(
                task_id=TaskId.generate(),
                title="完了タスク",
                description="",
                status=TaskStatus.DONE,
                priority=TaskPriority.NORMAL,
                source=TaskSource.MANUAL,
            )
        )
        project.tasks.append(
            Task(
                task_id=TaskId.generate(),
                title="未完了タスク",
                description="",
                status=TaskStatus.PENDING,
                priority=TaskPriority.NORMAL,
                source=TaskSource.MANUAL,
            )
        )
        await project_repo.save(project)

        result = await service.generate_phase_progress(project_id=str(project.project_id))

        assert len(result.phases) == 1
        assert result.phases[0].completion_rate == 0.5

    async def test_raises_for_unknown_project(self, service) -> None:
        with pytest.raises(ValueError, match="Project が見つかりません"):
            await service.generate_phase_progress(project_id=str(ProjectId.generate()))

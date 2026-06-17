"""
AssignService のユニットテスト。
"""

from __future__ import annotations

from datetime import date

import pytest

from src.application.assign.service import AssignService
from src.domain.audit.aggregate import AuditAction
from src.domain.member.aggregate import Member
from src.domain.member.value_objects import Availability, MemberId, MemberRole, PerformanceHistory
from src.domain.project.aggregate import Project
from src.domain.project.entities import Task
from src.domain.project.value_objects import (
    AssignmentStatus,
    ContextHubProjectRef,
    ProjectId,
    TaskId,
    TaskPriority,
    TaskSource,
    TaskStatus,
)
from src.infrastructure.audit.in_memory import InMemoryAuditLogRepository
from src.infrastructure.llm.mock_adapter import MockLLMAdapter
from src.infrastructure.repositories.in_memory import (
    InMemoryMemberRepository,
    InMemoryProjectMemberRepository,
    InMemoryProjectRepository,
)


def _make_project_with_tasks() -> Project:
    project = Project(
        project_id=ProjectId.generate(),
        name="テストプロジェクト",
        customer="顧客",
        goal="目標",
        context_hub_ref=ContextHubProjectRef(
            context_hub_project_id="hub-001",
            api_endpoint="http://localhost:8000/api/v1",
        ),
    )
    task = Task(
        task_id=TaskId.generate(),
        title="API 仕様ドラフト作成",
        description="テスト",
        status=TaskStatus.PENDING,
        priority=TaskPriority.HIGH,
        source=TaskSource.MEETING_EXTRACTION,
    )
    project.tasks.append(task)
    return project


def _make_member(name: str = "田中 太郎") -> Member:
    member = Member(
        member_id=MemberId.generate(),
        external_id=f"ext-{name}",
        name=name,
        role=MemberRole.DEVELOPER,
    )
    return member


@pytest.fixture
def project_repo() -> InMemoryProjectRepository:
    return InMemoryProjectRepository()


@pytest.fixture
def member_repo() -> InMemoryMemberRepository:
    return InMemoryMemberRepository()


@pytest.fixture
def pm_repo(member_repo) -> InMemoryProjectMemberRepository:
    return InMemoryProjectMemberRepository(member_repo)


@pytest.fixture
def llm() -> MockLLMAdapter:
    return MockLLMAdapter(fixed_response="稼働可能であり、期限内完了率が高いため適任です。")


@pytest.fixture
def audit_repo() -> InMemoryAuditLogRepository:
    return InMemoryAuditLogRepository()


@pytest.fixture
def service(project_repo, member_repo, llm, audit_repo) -> AssignService:
    return AssignService(
        project_repository=project_repo,
        member_repository=member_repo,
        llm_adapter=llm,
        audit_repository=audit_repo,
    )


async def _link(pm_repo: InMemoryProjectMemberRepository, project: Project, member: Member) -> None:
    """テストヘルパー: メンバーをプロジェクトに所属させる。"""
    await pm_repo.add(project.project_id, member.member_id)


@pytest.mark.asyncio
class TestGenerateDrafts:
    async def test_generates_draft_for_available_member(
        self,
        service: AssignService,
        project_repo: InMemoryProjectRepository,
        member_repo: InMemoryMemberRepository,
        pm_repo: InMemoryProjectMemberRepository,
    ) -> None:
        project = _make_project_with_tasks()
        member = _make_member()
        await project_repo.save(project)
        await member_repo.save(member)
        await _link(pm_repo, project, member)

        result = await service.generate_drafts(project_id=str(project.project_id))

        assert result.assignments_created == 1
        assert len(result.skipped_task_ids) == 0

        saved = await project_repo.find_by_id(project.project_id)
        assert len(saved.draft_assignments()) == 1
        assert saved.draft_assignments()[0].status == AssignmentStatus.DRAFT

    async def test_skips_task_when_no_members(
        self,
        service: AssignService,
        project_repo: InMemoryProjectRepository,
    ) -> None:
        project = _make_project_with_tasks()
        await project_repo.save(project)

        result = await service.generate_drafts(project_id=str(project.project_id))

        assert result.assignments_created == 0
        # no members = early return でタスクが skipped_task_ids に積まれる
        assert len(result.skipped_task_ids) == 1

    async def test_skips_member_with_zero_availability(
        self,
        service: AssignService,
        project_repo: InMemoryProjectRepository,
        member_repo: InMemoryMemberRepository,
        pm_repo: InMemoryProjectMemberRepository,
    ) -> None:
        project = _make_project_with_tasks()
        member = _make_member()
        # 今日の稼働時間を 0 に設定
        member.set_availability(Availability(date=date.today(), available_hours=0.0))
        await project_repo.save(project)
        await member_repo.save(member)
        await _link(pm_repo, project, member)

        result = await service.generate_drafts(project_id=str(project.project_id))

        assert result.assignments_created == 0
        assert len(result.skipped_task_ids) == 1

    async def test_raises_for_unknown_project(self, service: AssignService) -> None:
        with pytest.raises(ValueError, match="Project が見つかりません"):
            await service.generate_drafts(project_id=str(ProjectId.generate()))

    async def test_ai_rationale_is_set(
        self,
        service: AssignService,
        project_repo: InMemoryProjectRepository,
        member_repo: InMemoryMemberRepository,
        pm_repo: InMemoryProjectMemberRepository,
    ) -> None:
        project = _make_project_with_tasks()
        member = _make_member()
        await project_repo.save(project)
        await member_repo.save(member)
        await _link(pm_repo, project, member)

        await service.generate_drafts(project_id=str(project.project_id))

        saved = await project_repo.find_by_id(project.project_id)
        assert saved.draft_assignments()[0].ai_rationale != ""

    async def test_does_not_reassign_already_assigned_task(
        self,
        service: AssignService,
        project_repo: InMemoryProjectRepository,
        member_repo: InMemoryMemberRepository,
        pm_repo: InMemoryProjectMemberRepository,
    ) -> None:
        project = _make_project_with_tasks()
        member = _make_member()
        await project_repo.save(project)
        await member_repo.save(member)
        await _link(pm_repo, project, member)

        # 1回目: 割当案生成
        result1 = await service.generate_drafts(project_id=str(project.project_id))
        assert result1.assignments_created == 1

        # 2回目: すでに DRAFT があるのでスキップ
        result2 = await service.generate_drafts(project_id=str(project.project_id))
        assert result2.assignments_created == 0


@pytest.mark.asyncio
class TestSelectBestMember:
    def test_prefers_member_with_high_on_time_rate(self) -> None:
        good_member = _make_member("good")
        good_member.add_performance_record(
            PerformanceHistory(task_id="t1", completed_at=date.today(), delay_days=0)
        )
        poor_member = _make_member("poor")
        poor_member.add_performance_record(
            PerformanceHistory(task_id="t2", completed_at=date.today(), delay_days=10)
        )

        task = Task(
            task_id=TaskId.generate(),
            title="test",
            description="",
            status=TaskStatus.PENDING,
            priority=TaskPriority.NORMAL,
            source=TaskSource.MANUAL,
        )
        result = AssignService._select_best_member(task, [poor_member, good_member], date.today())
        assert result is good_member


@pytest.mark.asyncio
class TestAssignAuditLogging:
    async def test_records_assignment_created_audit(
        self,
        service: AssignService,
        project_repo: InMemoryProjectRepository,
        member_repo: InMemoryMemberRepository,
        pm_repo: InMemoryProjectMemberRepository,
        audit_repo: InMemoryAuditLogRepository,
    ) -> None:
        project = _make_project_with_tasks()
        await project_repo.save(project)
        member = _make_member()
        member.set_availability(Availability(date=date.today(), available_hours=6.0))
        await member_repo.save(member)
        await _link(pm_repo, project, member)

        result = await service.generate_drafts(project_id=str(project.project_id))

        created_logs = [
            log for log in audit_repo.all_logs if log.action == AuditAction.ASSIGNMENT_CREATED
        ]
        assert len(created_logs) == result.assignments_created
        assert created_logs[0].actor == "system"

    async def test_records_confirm_and_reject_audit(
        self,
        service: AssignService,
        project_repo: InMemoryProjectRepository,
        member_repo: InMemoryMemberRepository,
        pm_repo: InMemoryProjectMemberRepository,
        audit_repo: InMemoryAuditLogRepository,
    ) -> None:
        project = _make_project_with_tasks()
        # Task をもう 1 件追加（confirm + reject 両方を試すため）
        extra_task = Task(
            task_id=TaskId.generate(),
            title="追加タスク",
            description="",
            status=TaskStatus.PENDING,
            priority=TaskPriority.NORMAL,
            source=TaskSource.MANUAL,
        )
        project.tasks.append(extra_task)
        await project_repo.save(project)
        member = _make_member()
        member.set_availability(Availability(date=date.today(), available_hours=6.0))
        await member_repo.save(member)
        await _link(pm_repo, project, member)

        result = await service.generate_drafts(project_id=str(project.project_id))
        assert result.assignments_created >= 2

        await service.confirm_assignment(
            project_id=str(project.project_id),
            assignment_id=result.assignment_ids[0],
            confirmed_by="pl-user",
        )
        await service.reject_assignment(
            project_id=str(project.project_id),
            assignment_id=result.assignment_ids[1],
            rejected_by="pl-user",
        )

        confirmed = [
            log for log in audit_repo.all_logs if log.action == AuditAction.ASSIGNMENT_CONFIRMED
        ]
        rejected = [
            log for log in audit_repo.all_logs if log.action == AuditAction.ASSIGNMENT_REJECTED
        ]
        assert len(confirmed) == 1
        assert confirmed[0].actor == "pl-user"
        assert len(rejected) == 1
        assert rejected[0].actor == "pl-user"

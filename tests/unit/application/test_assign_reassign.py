"""AssignService.propose_reassignments のユニットテスト。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.application.assign.service import AssignService
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
from src.infrastructure.llm.mock_adapter import MockLLMAdapter
from src.infrastructure.repositories.in_memory import (
    InMemoryMemberRepository,
    InMemoryProjectMemberRepository,
    InMemoryProjectRepository,
)

pytestmark = pytest.mark.asyncio


def _project() -> Project:
    return Project(
        project_id=ProjectId.generate(),
        name="PJ",
        customer="顧客",
        goal="目標",
        context_hub_ref=ContextHubProjectRef(
            context_hub_project_id="hub-001", api_endpoint="http://localhost:8000"
        ),
    )


def _member(name: str) -> Member:
    return Member(
        member_id=MemberId.generate(),
        external_id=f"ext-{name}",
        name=name,
        role=MemberRole.DEVELOPER,
    )


def _overdue_task() -> Task:
    return Task(
        task_id=TaskId.generate(),
        title="遅延タスク",
        description="",
        status=TaskStatus.IN_PROGRESS,
        priority=TaskPriority.HIGH,
        source=TaskSource.MANUAL,
        due_date=date.today() - timedelta(days=2),
    )


async def _service_with(project: Project, members: list[Member]):
    project_repo = InMemoryProjectRepository()
    member_repo = InMemoryMemberRepository()
    pm_repo = InMemoryProjectMemberRepository(member_repo)
    await project_repo.save(project)
    for m in members:
        await member_repo.save(m)
        await pm_repo.add(project.project_id, m.member_id)
    service = AssignService(
        project_repository=project_repo,
        member_repository=member_repo,
        llm_adapter=MockLLMAdapter(fixed_response="代替候補が適任です。"),
    )
    return service, project_repo


class TestProposeReassignments:
    async def test_creates_draft_reassignment_to_other_member(self) -> None:
        project = _project()
        task = _overdue_task()
        project.tasks.append(task)
        current = _member("現担当")
        candidate = _member("代替")
        # current に confirmed 割当
        assignment = Assignment(
            assignment_id=AssignmentId.generate(),
            task_id=task.task_id,
            member_id=str(current.member_id),
            status=AssignmentStatus.CONFIRMED,
            ai_rationale="初期割当",
        )
        project.assignments.append(assignment)

        service, project_repo = await _service_with(project, [current, candidate])
        result = await service.propose_reassignments(str(project.project_id), [str(task.task_id)])

        assert result.reassignments_created == 1
        saved = await project_repo.find_by_id(project.project_id)
        drafts = saved.draft_assignments()
        assert len(drafts) == 1
        assert drafts[0].member_id == str(candidate.member_id)
        assert drafts[0].ai_rationale.startswith("[入替案]")

    async def test_skips_unassigned_task(self) -> None:
        project = _project()
        task = _overdue_task()
        project.tasks.append(task)
        member = _member("誰か")

        service, _ = await _service_with(project, [member])
        result = await service.propose_reassignments(str(project.project_id), [str(task.task_id)])

        # 未割当タスクは入替対象外（新規割当の責務）
        assert result.reassignments_created == 0
        assert str(task.task_id) in result.skipped_task_ids

    async def test_skips_when_no_other_candidate(self) -> None:
        project = _project()
        task = _overdue_task()
        project.tasks.append(task)
        current = _member("唯一")
        project.assignments.append(
            Assignment(
                assignment_id=AssignmentId.generate(),
                task_id=task.task_id,
                member_id=str(current.member_id),
                status=AssignmentStatus.CONFIRMED,
                ai_rationale="初期割当",
            )
        )

        service, _ = await _service_with(project, [current])
        result = await service.propose_reassignments(str(project.project_id), [str(task.task_id)])

        assert result.reassignments_created == 0
        assert str(task.task_id) in result.skipped_task_ids

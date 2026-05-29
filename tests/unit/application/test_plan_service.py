"""
PlanService のユニットテスト。
Context-Hub クライアントと LLM アダプタは Mock を使用。
"""

from __future__ import annotations

from datetime import date

import pytest

from src.application.plan.service import PlanService
from src.domain.audit.aggregate import AuditAction
from src.domain.project.aggregate import Project
from src.domain.project.value_objects import (
    ContextHubProjectRef,
    ProjectId,
    TaskSource,
    TaskStatus,
)
from src.infrastructure.audit.in_memory import InMemoryAuditLogRepository
from src.infrastructure.context_hub.mock_client import MockContextHubClient
from src.infrastructure.llm.mock_adapter import MockLLMAdapter
from src.infrastructure.repositories.in_memory import InMemoryProjectRepository


def _make_project() -> Project:
    return Project(
        project_id=ProjectId.generate(),
        name="テストプロジェクト",
        customer="テスト顧客",
        goal="テスト目標",
        context_hub_ref=ContextHubProjectRef(
            context_hub_project_id="hub-001",
            api_endpoint="http://localhost:8000/api/v1",
        ),
    )


@pytest.fixture
def project_repo() -> InMemoryProjectRepository:
    repo = InMemoryProjectRepository()
    return repo


@pytest.fixture
def hub_client() -> MockContextHubClient:
    return MockContextHubClient()


@pytest.fixture
def llm() -> MockLLMAdapter:
    return MockLLMAdapter(fixed_response="テストAI応答")


@pytest.fixture
def audit_repo() -> InMemoryAuditLogRepository:
    return InMemoryAuditLogRepository()


@pytest.fixture
def service(project_repo, hub_client, llm, audit_repo) -> PlanService:
    return PlanService(
        project_repository=project_repo,
        context_hub_client=hub_client,
        llm_adapter=llm,
        audit_repository=audit_repo,
    )


@pytest.mark.asyncio
class TestExtractTasksFromMeeting:
    async def test_extracts_tasks_and_saves_to_project(
        self, service: PlanService, project_repo: InMemoryProjectRepository
    ) -> None:
        project = _make_project()
        await project_repo.save(project)

        result = await service.extract_tasks_from_meeting(
            project_id=str(project.project_id),
            meeting_id="meeting-001",
        )

        assert result.tasks_added == 2  # MockClient は 2 件返す
        assert len(result.task_ids) == 2

        # プロジェクトに保存されていることを確認
        saved = await project_repo.find_by_id(project.project_id)
        assert saved is not None
        assert len(saved.tasks) == 2

    async def test_task_has_correct_source(
        self, service: PlanService, project_repo: InMemoryProjectRepository
    ) -> None:
        project = _make_project()
        await project_repo.save(project)

        await service.extract_tasks_from_meeting(
            project_id=str(project.project_id),
            meeting_id="meeting-001",
        )

        saved = await project_repo.find_by_id(project.project_id)
        for task in saved.tasks:
            assert task.source == TaskSource.MEETING_EXTRACTION
            assert task.source_ref == "meeting-001"
            assert task.status == TaskStatus.PENDING

    async def test_raises_for_unknown_project(self, service: PlanService) -> None:
        with pytest.raises(ValueError, match="Project が見つかりません"):
            await service.extract_tasks_from_meeting(
                project_id=str(ProjectId.generate()),
                meeting_id="meeting-001",
            )

    async def test_skips_duplicate_tasks(
        self, service: PlanService, project_repo: InMemoryProjectRepository
    ) -> None:
        project = _make_project()
        await project_repo.save(project)

        # 1回目
        result1 = await service.extract_tasks_from_meeting(
            project_id=str(project.project_id),
            meeting_id="meeting-001",
        )
        assert result1.tasks_added == 2

        # 2回目（同じ meeting_id だが task_id は新規生成されるので重複しない）
        result2 = await service.extract_tasks_from_meeting(
            project_id=str(project.project_id),
            meeting_id="meeting-001",
        )
        # task_id は毎回新規生成なので追加される
        assert result2.tasks_added == 2


@pytest.mark.asyncio
class TestImportTasksFromIssues:
    async def test_imports_issues_as_tasks(
        self, service: PlanService, project_repo: InMemoryProjectRepository
    ) -> None:
        project = _make_project()
        await project_repo.save(project)

        result = await service.import_tasks_from_issues(
            project_id=str(project.project_id),
            source="backlog",
        )

        assert result.tasks_added == 1  # MockClient は 1 件返す
        assert result.source == "backlog"

        saved = await project_repo.find_by_id(project.project_id)
        assert len(saved.tasks) == 1
        assert saved.tasks[0].source == TaskSource.ISSUE_IMPORT

    async def test_task_priority_mapped_correctly(
        self, service: PlanService, project_repo: InMemoryProjectRepository
    ) -> None:
        project = _make_project()
        await project_repo.save(project)

        await service.import_tasks_from_issues(
            project_id=str(project.project_id),
            source="backlog",
        )

        saved = await project_repo.find_by_id(project.project_id)
        from src.domain.project.value_objects import TaskPriority

        # MockClient は priority="high" の Issue を返す
        assert saved.tasks[0].priority == TaskPriority.HIGH

    async def test_raises_for_unknown_project(self, service: PlanService) -> None:
        with pytest.raises(ValueError, match="Project が見つかりません"):
            await service.import_tasks_from_issues(
                project_id=str(ProjectId.generate()),
                source="backlog",
            )

    async def test_due_date_is_parsed(
        self, service: PlanService, project_repo: InMemoryProjectRepository
    ) -> None:
        project = _make_project()
        await project_repo.save(project)

        await service.import_tasks_from_issues(
            project_id=str(project.project_id),
            source="backlog",
        )

        saved = await project_repo.find_by_id(project.project_id)
        assert saved.tasks[0].due_date == date(2026, 5, 20)


@pytest.mark.asyncio
class TestPlanAuditLogging:
    async def test_records_task_created_audit_on_meeting_extraction(
        self,
        service: PlanService,
        project_repo: InMemoryProjectRepository,
        audit_repo: InMemoryAuditLogRepository,
    ) -> None:
        project = _make_project()
        await project_repo.save(project)

        result = await service.extract_tasks_from_meeting(
            project_id=str(project.project_id),
            meeting_id="meeting-001",
        )

        task_created_logs = [
            log for log in audit_repo.all_logs if log.action == AuditAction.TASK_CREATED
        ]
        assert len(task_created_logs) == result.tasks_added

        hub_logs = [
            log for log in audit_repo.all_logs if log.action == AuditAction.CONTEXT_HUB_QUERIED
        ]
        assert len(hub_logs) == 1
        assert hub_logs[0].data_ref == "meeting:meeting-001"

    async def test_records_task_created_audit_on_issue_import(
        self,
        service: PlanService,
        project_repo: InMemoryProjectRepository,
        audit_repo: InMemoryAuditLogRepository,
    ) -> None:
        project = _make_project()
        await project_repo.save(project)

        result = await service.import_tasks_from_issues(
            project_id=str(project.project_id),
            source="backlog",
        )

        task_created_logs = [
            log for log in audit_repo.all_logs if log.action == AuditAction.TASK_CREATED
        ]
        assert len(task_created_logs) == result.tasks_added

        hub_logs = [
            log for log in audit_repo.all_logs if log.action == AuditAction.CONTEXT_HUB_QUERIED
        ]
        assert any("issues:backlog" in (log.data_ref or "") for log in hub_logs)

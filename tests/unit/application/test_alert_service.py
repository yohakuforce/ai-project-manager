"""
AlertService のユニットテスト。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.application.alert.service import AlertService
from src.domain.alert.aggregate import AlertCategory, AlertStatus
from src.domain.audit.aggregate import AuditAction
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
from src.infrastructure.audit.in_memory import InMemoryAuditLogRepository
from src.infrastructure.llm.mock_adapter import MockLLMAdapter
from src.infrastructure.notifiers.in_memory import InMemoryNotifier
from src.infrastructure.repositories.in_memory import (
    InMemoryAlertRepository,
    InMemoryDailyReportRepository,
    InMemoryMemberRepository,
    InMemoryProjectRepository,
)


def _make_project_with_overdue_task() -> Project:
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
    overdue_task = Task(
        task_id=TaskId.generate(),
        title="遅延タスク",
        description="",
        status=TaskStatus.IN_PROGRESS,
        priority=TaskPriority.HIGH,
        source=TaskSource.MANUAL,
        due_date=date.today() - timedelta(days=3),
    )
    project.tasks.append(overdue_task)
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
    return MockLLMAdapter(fixed_response="タスクが遅延しています。対応を確認してください。")


@pytest.fixture
def notifier():
    return InMemoryNotifier()


@pytest.fixture
def audit_repo():
    return InMemoryAuditLogRepository()


@pytest.fixture
def service(project_repo, member_repo, alert_repo, report_repo, llm, notifier, audit_repo):
    return AlertService(
        project_repository=project_repo,
        member_repository=member_repo,
        alert_repository=alert_repo,
        daily_report_repository=report_repo,
        llm_adapter=llm,
        notifier=notifier,
        alert_channel="#ai-pm-alerts",
        audit_repository=audit_repo,
    )


@pytest.mark.asyncio
class TestScanProject:
    async def test_detects_overdue_task_alert(
        self, service, project_repo, alert_repo, notifier
    ) -> None:
        project = _make_project_with_overdue_task()
        await project_repo.save(project)

        result = await service.scan_project(project_id=str(project.project_id))

        assert result.alerts_created >= 1
        assert AlertCategory.TASK_DELAY.value in result.alert_categories
        assert result.notifications_sent == result.alerts_created
        assert result.notifications_failed == 0
        sent_alerts = notifier.filter("alert")
        assert len(sent_alerts) == result.alerts_created
        assert sent_alerts[0].channel == "#ai-pm-alerts"
        assert sent_alerts[0].payload.project_name == project.name

    async def test_does_not_duplicate_active_alert(self, service, project_repo, alert_repo) -> None:
        project = _make_project_with_overdue_task()
        await project_repo.save(project)

        # 1回目スキャン
        result1 = await service.scan_project(project_id=str(project.project_id))
        assert result1.alerts_created >= 1

        # 2回目スキャン: 既存アクティブアラートがあるので重複しない
        result2 = await service.scan_project(project_id=str(project.project_id))
        assert result2.alerts_created == 0

    async def test_raises_for_unknown_project(self, service) -> None:
        with pytest.raises(ValueError, match="Project が見つかりません"):
            await service.scan_project(project_id=str(ProjectId.generate()))

    async def test_counts_failed_notifications_when_notifier_errors(
        self, project_repo, member_repo, alert_repo, report_repo, llm
    ) -> None:
        failing_notifier = InMemoryNotifier(fail_on_send=True)
        svc = AlertService(
            project_repository=project_repo,
            member_repository=member_repo,
            alert_repository=alert_repo,
            daily_report_repository=report_repo,
            llm_adapter=llm,
            notifier=failing_notifier,
        )
        project = _make_project_with_overdue_task()
        await project_repo.save(project)

        result = await svc.scan_project(project_id=str(project.project_id))

        assert result.alerts_created >= 1
        assert result.notifications_sent == 0
        assert result.notifications_failed == result.alerts_created

    async def test_no_notifier_does_not_emit(
        self, project_repo, member_repo, alert_repo, report_repo, llm
    ) -> None:
        svc = AlertService(
            project_repository=project_repo,
            member_repository=member_repo,
            alert_repository=alert_repo,
            daily_report_repository=report_repo,
            llm_adapter=llm,
            notifier=None,
        )
        project = _make_project_with_overdue_task()
        await project_repo.save(project)

        result = await svc.scan_project(project_id=str(project.project_id))

        assert result.alerts_created >= 1
        assert result.notifications_sent == 0
        assert result.notifications_failed == 0

    async def test_detects_no_alerts_for_healthy_project(self, service, project_repo) -> None:
        # タスクなしのプロジェクト
        project = Project(
            project_id=ProjectId.generate(),
            name="健全PJ",
            customer="顧客",
            goal="目標",
            context_hub_ref=ContextHubProjectRef(
                context_hub_project_id="hub-002",
                api_endpoint="http://localhost:8000",
            ),
        )
        await project_repo.save(project)

        result = await service.scan_project(project_id=str(project.project_id))

        assert result.alerts_created == 0


@pytest.mark.asyncio
class TestAcknowledgeAlert:
    async def test_acknowledges_existing_alert(self, service, project_repo, alert_repo) -> None:
        project = _make_project_with_overdue_task()
        await project_repo.save(project)

        scan_result = await service.scan_project(project_id=str(project.project_id))
        alert_id = scan_result.alert_ids[0]

        ack_result = await service.acknowledge_alert(
            alert_id=alert_id,
            acknowledged_by="pl-user-001",
        )

        assert ack_result.acknowledged_by == "pl-user-001"

        import uuid

        from src.domain.alert.aggregate import AlertId

        aid = AlertId(value=uuid.UUID(alert_id))
        saved_alert = await alert_repo.find_by_id(aid)
        assert saved_alert.status == AlertStatus.ACKNOWLEDGED

    async def test_raises_for_unknown_alert(self, service) -> None:
        import uuid

        with pytest.raises(ValueError, match="Alert が見つかりません"):
            await service.acknowledge_alert(
                alert_id=str(uuid.uuid4()),
                acknowledged_by="user",
            )


@pytest.mark.asyncio
class TestAuditLogging:
    async def test_records_alert_created_audit(self, service, project_repo, audit_repo) -> None:
        project = _make_project_with_overdue_task()
        await project_repo.save(project)

        result = await service.scan_project(project_id=str(project.project_id))

        created_actions = [
            log.action for log in audit_repo.all_logs if log.action == AuditAction.ALERT_CREATED
        ]
        assert len(created_actions) == result.alerts_created

    async def test_records_alert_acknowledged_audit(
        self, service, project_repo, audit_repo
    ) -> None:
        project = _make_project_with_overdue_task()
        await project_repo.save(project)
        scan_result = await service.scan_project(project_id=str(project.project_id))
        alert_id = scan_result.alert_ids[0]

        await service.acknowledge_alert(alert_id=alert_id, acknowledged_by="pl-user")

        ack_logs = [
            log for log in audit_repo.all_logs if log.action == AuditAction.ALERT_ACKNOWLEDGED
        ]
        assert len(ack_logs) == 1
        assert ack_logs[0].actor == "pl-user"
        assert ack_logs[0].data_ref == alert_id

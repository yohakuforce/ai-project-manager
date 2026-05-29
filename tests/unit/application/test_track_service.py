"""
TrackService のユニットテスト。
"""

from __future__ import annotations

from datetime import date

import pytest

from src.application.track.service import ResponseInput, TrackService
from src.domain.audit.aggregate import AuditAction
from src.domain.member.aggregate import Member
from src.domain.member.value_objects import MemberId, MemberRole
from src.domain.project.aggregate import Project
from src.domain.project.value_objects import (
    ContextHubProjectRef,
    ProjectId,
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
from src.infrastructure.audit.in_memory import InMemoryAuditLogRepository
from src.infrastructure.llm.mock_adapter import MockLLMAdapter
from src.infrastructure.notifiers.in_memory import InMemoryNotifier
from src.infrastructure.repositories.in_memory import (
    InMemoryDailyReportRepository,
    InMemoryMemberRepository,
    InMemoryProjectRepository,
)


def _make_project() -> Project:
    return Project(
        project_id=ProjectId.generate(),
        name="テストPJ",
        customer="顧客",
        goal="目標",
        context_hub_ref=ContextHubProjectRef(
            context_hub_project_id="hub-001",
            api_endpoint="http://localhost:8000/api/v1",
        ),
    )


def _make_member() -> Member:
    return Member(
        member_id=MemberId.generate(),
        external_id="ext-001",
        name="田中 太郎",
        role=MemberRole.DEVELOPER,
    )


def _make_report_with_questions(member_id: str, project_id: str) -> DailyReport:
    q1 = ReportQuestion(
        question_id=QuestionId.generate(),
        question_type=QuestionType.PROGRESS_PERCENT,
        body="進捗率は？",
    )
    q2 = ReportQuestion(
        question_id=QuestionId.generate(),
        question_type=QuestionType.BLOCKER,
        body="ブロッカーは？",
    )
    template = ReportTemplate.create([q1, q2])
    return DailyReport(
        report_id=DailyReportId.generate(),
        member_id=member_id,
        project_id=project_id,
        report_date=date.today(),
        template=template,
        status=ReportStatus.DELIVERED,
    )


@pytest.fixture
def project_repo():
    return InMemoryProjectRepository()


@pytest.fixture
def member_repo():
    return InMemoryMemberRepository()


@pytest.fixture
def report_repo():
    return InMemoryDailyReportRepository()


@pytest.fixture
def llm():
    return MockLLMAdapter(fixed_response="進捗 70%。ブロッカーなし。")


@pytest.fixture
def notifier():
    return InMemoryNotifier()


@pytest.fixture
def audit_repo():
    return InMemoryAuditLogRepository()


@pytest.fixture
def service(project_repo, member_repo, report_repo, llm, notifier, audit_repo):
    return TrackService(
        project_repository=project_repo,
        member_repository=member_repo,
        daily_report_repository=report_repo,
        llm_adapter=llm,
        notifier=notifier,
        audit_repository=audit_repo,
    )


@pytest.mark.asyncio
class TestGenerateDailyReportTemplates:
    async def test_generates_report_for_each_member(
        self, service, project_repo, member_repo, report_repo
    ) -> None:
        project = _make_project()
        member = _make_member()
        await project_repo.save(project)
        await member_repo.save(member)

        result = await service.generate_daily_report_templates(project_id=str(project.project_id))

        assert result.reports_generated == 1
        assert len(result.report_ids) == 1

    async def test_skips_if_report_already_exists(
        self, service, project_repo, member_repo, report_repo
    ) -> None:
        project = _make_project()
        member = _make_member()
        await project_repo.save(project)
        await member_repo.save(member)

        # 1回目生成
        await service.generate_daily_report_templates(project_id=str(project.project_id))
        # 2回目: スキップ
        result2 = await service.generate_daily_report_templates(project_id=str(project.project_id))
        assert result2.reports_generated == 0

    async def test_raises_for_unknown_project(self, service) -> None:
        with pytest.raises(ValueError, match="Project が見つかりません"):
            await service.generate_daily_report_templates(project_id=str(ProjectId.generate()))


@pytest.mark.asyncio
class TestDeliverReports:
    async def test_marks_pending_reports_delivered_and_notifies(
        self, service, member_repo, report_repo, notifier
    ) -> None:
        member = _make_member()
        await member_repo.save(member)
        report = _make_report_with_questions(str(member.member_id), "project-001")
        # 配信前は PENDING にする
        report.status = ReportStatus.PENDING
        await report_repo.save(report)

        result = await service.deliver_reports(project_id="project-001")

        assert result.reports_delivered == 1
        assert result.notifications_sent == 1
        assert result.notifications_failed == 0

        sent = notifier.filter("daily_report")
        assert len(sent) == 1
        assert sent[0].channel == member.external_id
        assert sent[0].payload.member_name == member.name

        saved = await report_repo.find_by_id(report.report_id)
        assert saved.status == ReportStatus.DELIVERED

    async def test_counts_failure_when_member_missing(self, service, report_repo, notifier) -> None:
        # メンバー未登録の状態で配信
        report = _make_report_with_questions("not-a-uuid", "project-001")
        report.status = ReportStatus.PENDING
        await report_repo.save(report)

        result = await service.deliver_reports(project_id="project-001")

        assert result.reports_delivered == 1
        assert result.notifications_sent == 0
        assert result.notifications_failed == 1
        assert notifier.sent == []

    async def test_no_notifier_does_not_emit(
        self, project_repo, member_repo, report_repo, llm
    ) -> None:
        service_no_notifier = TrackService(
            project_repository=project_repo,
            member_repository=member_repo,
            daily_report_repository=report_repo,
            llm_adapter=llm,
            notifier=None,
        )
        member = _make_member()
        await member_repo.save(member)
        report = _make_report_with_questions(str(member.member_id), "project-001")
        report.status = ReportStatus.PENDING
        await report_repo.save(report)

        result = await service_no_notifier.deliver_reports(project_id="project-001")

        assert result.reports_delivered == 1
        assert result.notifications_sent == 0
        assert result.notifications_failed == 0


@pytest.mark.asyncio
class TestSubmitResponses:
    async def test_saves_responses_and_marks_submitted(self, service, report_repo) -> None:
        report = _make_report_with_questions("member-001", "project-001")
        await report_repo.save(report)

        q_ids = [str(q.question_id) for q in report.template.questions]
        inputs = [
            ResponseInput(question_id=q_ids[0], response_text="70"),
            ResponseInput(question_id=q_ids[1], response_text="なし"),
        ]

        result = await service.submit_responses(
            report_id=str(report.report_id),
            responses=inputs,
            finalize=True,
        )

        assert result.responses_saved == 2
        assert result.is_complete is True

        saved = await report_repo.find_by_id(report.report_id)
        assert saved.status == ReportStatus.SUBMITTED

    async def test_raises_for_unknown_report(self, service) -> None:
        import uuid

        with pytest.raises(ValueError, match="DailyReport が見つかりません"):
            await service.submit_responses(
                report_id=str(uuid.uuid4()),
                responses=[],
            )


@pytest.mark.asyncio
class TestAuditLogging:
    async def test_records_report_delivered_audit(
        self, service, member_repo, report_repo, audit_repo
    ) -> None:
        member = _make_member()
        await member_repo.save(member)
        report = _make_report_with_questions(str(member.member_id), "project-001")
        report.status = ReportStatus.PENDING
        await report_repo.save(report)

        await service.deliver_reports(project_id="project-001")

        delivered_logs = [
            log for log in audit_repo.all_logs if log.action == AuditAction.REPORT_DELIVERED
        ]
        assert len(delivered_logs) == 1
        assert delivered_logs[0].data_ref == str(report.report_id)

    async def test_records_report_submitted_audit_on_finalize(
        self, service, report_repo, audit_repo
    ) -> None:
        report = _make_report_with_questions("member-001", "project-001")
        await report_repo.save(report)
        q_ids = [str(q.question_id) for q in report.template.questions]

        await service.submit_responses(
            report_id=str(report.report_id),
            responses=[
                ResponseInput(question_id=q_ids[0], response_text="70"),
                ResponseInput(question_id=q_ids[1], response_text="なし"),
            ],
            finalize=True,
        )

        submitted_logs = [
            log for log in audit_repo.all_logs if log.action == AuditAction.REPORT_SUBMITTED
        ]
        assert len(submitted_logs) == 1
        assert submitted_logs[0].actor == "member-001"


@pytest.mark.asyncio
class TestAnalyzeResponses:
    async def test_sets_ai_summary_and_returns_result(self, service, report_repo) -> None:
        report = _make_report_with_questions("member-001", "project-001")
        await report_repo.save(report)

        # 先に回答を送信
        q_ids = [str(q.question_id) for q in report.template.questions]
        await service.submit_responses(
            report_id=str(report.report_id),
            responses=[
                ResponseInput(question_id=q_ids[0], response_text="80"),
                ResponseInput(question_id=q_ids[1], response_text="なし"),
            ],
        )

        result = await service.analyze_responses(report_id=str(report.report_id))

        assert result.ai_summary != ""
        saved = await report_repo.find_by_id(report.report_id)
        assert saved.status == ReportStatus.ANALYZED

    async def test_detects_blockers_in_responses(self, service, report_repo) -> None:
        report = _make_report_with_questions("member-001", "project-001")
        await report_repo.save(report)

        q_ids = [str(q.question_id) for q in report.template.questions]
        await service.submit_responses(
            report_id=str(report.report_id),
            responses=[
                ResponseInput(question_id=q_ids[0], response_text="50"),
                ResponseInput(question_id=q_ids[1], response_text="顧客への確認が必要"),
            ],
        )

        result = await service.analyze_responses(report_id=str(report.report_id))

        assert "顧客への確認が必要" in result.blockers_detected

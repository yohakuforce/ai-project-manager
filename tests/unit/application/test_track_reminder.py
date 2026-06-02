"""TrackService.remind_unsubmitted のユニットテスト。"""

from __future__ import annotations

from datetime import date

import pytest

from src.application.track.service import TrackService
from src.domain.member.aggregate import Member
from src.domain.member.value_objects import MemberId, MemberRole
from src.domain.project.aggregate import Project
from src.domain.project.value_objects import ContextHubProjectRef, ProjectId
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
    InMemoryDailyReportRepository,
    InMemoryMemberRepository,
    InMemoryProjectRepository,
)

pytestmark = pytest.mark.asyncio

REPORT_DATE = date(2026, 6, 2)


def _template() -> ReportTemplate:
    return ReportTemplate.create(
        [
            ReportQuestion(
                question_id=QuestionId.generate(),
                question_type=QuestionType.FREE_TEXT,
                body="本日の作業は？",
            )
        ]
    )


def _report(member_id: str, status: ReportStatus, project_id: str) -> DailyReport:
    return DailyReport(
        report_id=DailyReportId.generate(),
        member_id=member_id,
        project_id=project_id,
        report_date=REPORT_DATE,
        template=_template(),
        status=status,
    )


async def _service():
    project_repo = InMemoryProjectRepository()
    member_repo = InMemoryMemberRepository()
    report_repo = InMemoryDailyReportRepository()
    notifier = InMemoryNotifier()

    project = Project(
        project_id=ProjectId.from_str("11111111-1111-1111-1111-111111111111"),
        name="PJ",
        customer="顧客",
        goal="目標",
        context_hub_ref=ContextHubProjectRef(context_hub_project_id="hub", api_endpoint="http://x"),
    )
    await project_repo.save(project)
    service = TrackService(
        project_repository=project_repo,
        member_repository=member_repo,
        daily_report_repository=report_repo,
        llm_adapter=MockLLMAdapter(),
        notifier=notifier,
        default_channel="#fallback",
        leader_channel="#leader",
    )
    return service, member_repo, report_repo, notifier, str(project.project_id)


class TestRemindUnsubmitted:
    async def test_reminds_members_and_notifies_leader(self) -> None:
        service, member_repo, report_repo, notifier, pid = await _service()
        m1 = Member(
            member_id=MemberId.generate(),
            external_id="@yamada",
            name="山田",
            role=MemberRole.DEVELOPER,
        )
        await member_repo.save(m1)
        await report_repo.save(_report(str(m1.member_id), ReportStatus.DELIVERED, pid))

        result = await service.remind_unsubmitted(pid, REPORT_DATE)

        assert result.unsubmitted_member_ids == [str(m1.member_id)]
        assert result.member_reminders_sent == 1
        assert result.leader_notified is True

        messages = notifier.filter("message")
        kinds_channels = {(m.payload.kind, m.channel) for m in messages}
        # 本人 DM（@yamada）とリーダー（#leader）の両方に reminder が出る
        assert ("reminder", "@yamada") in kinds_channels
        assert ("reminder", "#leader") in kinds_channels

    async def test_no_unsubmitted_sends_nothing(self) -> None:
        service, member_repo, report_repo, notifier, pid = await _service()
        m1 = Member(
            member_id=MemberId.generate(),
            external_id="@ok",
            name="提出済",
            role=MemberRole.DEVELOPER,
        )
        await member_repo.save(m1)
        await report_repo.save(_report(str(m1.member_id), ReportStatus.SUBMITTED, pid))

        result = await service.remind_unsubmitted(pid, REPORT_DATE)

        assert result.unsubmitted_member_ids == []
        assert result.member_reminders_sent == 0
        assert result.leader_notified is False
        assert notifier.filter("message") == []

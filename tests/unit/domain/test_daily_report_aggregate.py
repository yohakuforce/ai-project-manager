"""DailyReport 集約のユニットテスト。"""

from __future__ import annotations

from datetime import date

import pytest

from src.domain.reporting import (
    DailyReport,
    DailyReportId,
    DailyReportSubmitted,
    QuestionId,
    QuestionType,
    ReportQuestion,
    ReportStatus,
    ReportTemplate,
)


def make_report(questions: list[ReportQuestion] | None = None) -> DailyReport:
    if questions is None:
        questions = [
            ReportQuestion(
                question_id=QuestionId.generate(),
                question_type=QuestionType.PROGRESS_PERCENT,
                body="今日のタスク進捗は何%ですか？",
                task_id="task-001",
            )
        ]
    return DailyReport(
        report_id=DailyReportId.generate(),
        member_id="member-001",
        project_id="project-001",
        report_date=date.today(),
        template=ReportTemplate.create(questions),
    )


class TestDailyReportLifecycle:
    def test_initial_status_is_pending(self) -> None:
        report = make_report()
        assert report.status == ReportStatus.PENDING

    def test_mark_delivered_changes_status(self) -> None:
        report = make_report()
        report.mark_delivered()
        assert report.status == ReportStatus.DELIVERED
        assert report.delivered_at is not None

    def test_finalize_submission_changes_status(self) -> None:
        report = make_report()
        report.mark_delivered()
        report.finalize_submission()
        assert report.status == ReportStatus.SUBMITTED
        assert report.submitted_at is not None

    def test_finalize_submission_raises_domain_event(self) -> None:
        report = make_report()
        report.mark_delivered()
        report.finalize_submission()
        events = report.pop_domain_events()
        assert len(events) == 1
        assert isinstance(events[0], DailyReportSubmitted)
        assert events[0].member_id == "member-001"

    def test_set_ai_summary_changes_status_to_analyzed(self) -> None:
        report = make_report()
        report.mark_delivered()
        report.finalize_submission()
        report.set_ai_summary("AI 解析サマリ")
        assert report.status == ReportStatus.ANALYZED
        assert report.ai_summary == "AI 解析サマリ"

    def test_cannot_finalize_already_submitted_report(self) -> None:
        report = make_report()
        report.mark_delivered()
        report.finalize_submission()
        with pytest.raises(ValueError):
            report.finalize_submission()


class TestReportResponses:
    def test_submit_response_adds_response(self) -> None:
        question_id = QuestionId.generate()
        questions = [
            ReportQuestion(
                question_id=question_id,
                question_type=QuestionType.PROGRESS_PERCENT,
                body="進捗は？",
                task_id="task-001",
            )
        ]
        report = make_report(questions)
        report.submit_response(question_id, "70%")
        assert len(report.responses) == 1
        assert report.responses[0].response_text == "70%"

    def test_submit_response_overwrites_existing(self) -> None:
        question_id = QuestionId.generate()
        questions = [
            ReportQuestion(
                question_id=question_id,
                question_type=QuestionType.PROGRESS_PERCENT,
                body="進捗は？",
            )
        ]
        report = make_report(questions)
        report.submit_response(question_id, "50%")
        report.submit_response(question_id, "80%")  # 上書き
        assert len(report.responses) == 1
        assert report.responses[0].response_text == "80%"

    def test_unanswered_questions_returns_unanswered(self) -> None:
        q1 = QuestionId.generate()
        q2 = QuestionId.generate()
        questions = [
            ReportQuestion(question_id=q1, question_type=QuestionType.PROGRESS_PERCENT, body="Q1"),
            ReportQuestion(question_id=q2, question_type=QuestionType.BLOCKER, body="Q2"),
        ]
        report = make_report(questions)
        report.submit_response(q1, "70%")

        unanswered = report.unanswered_questions()
        assert len(unanswered) == 1
        assert unanswered[0].question_id == q2

    def test_is_fully_answered_returns_true_when_all_answered(self) -> None:
        q1 = QuestionId.generate()
        questions = [
            ReportQuestion(question_id=q1, question_type=QuestionType.PROGRESS_PERCENT, body="Q1"),
        ]
        report = make_report(questions)
        report.submit_response(q1, "100%")
        assert report.is_fully_answered() is True

"""DailyReport 集約ルート。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from .value_objects import (
    DailyReportId,
    QuestionId,
    ReportResponse,
    ReportStatus,
    ReportTemplate,
)


@dataclass(frozen=True)
class DailyReportSubmitted:
    """ドメインイベント: メンバーが日報を送信した。"""

    report_id: DailyReportId
    member_id: str
    project_id: str


@dataclass
class DailyReport:
    """
    DailyReport 集約ルート。
    1 Member × 1 日 = 1 DailyReport。
    AI が Template を生成し、Member が Responses を埋める。
    """

    report_id: DailyReportId
    member_id: str  # MemberId の文字列表現（集約間は ID 参照のみ）
    project_id: str  # ProjectId の文字列表現
    report_date: date
    template: ReportTemplate
    responses: list[ReportResponse] = field(default_factory=list)
    ai_summary: str | None = None
    status: ReportStatus = ReportStatus.PENDING
    delivered_at: datetime | None = None
    submitted_at: datetime | None = None
    analyzed_at: datetime | None = None

    _domain_events: list = field(default_factory=list, repr=False, compare=False)

    def mark_delivered(self) -> None:
        """配信完了を記録する。"""
        self.status = ReportStatus.DELIVERED
        self.delivered_at = datetime.now(UTC)

    def submit_response(self, question_id: QuestionId, response_text: str) -> None:
        """メンバーが回答を送信する。既存の回答は上書きする。"""
        # 同一 question_id の既存回答を除去
        self.responses = [r for r in self.responses if r.question_id != question_id]
        self.responses.append(
            ReportResponse.create(question_id=question_id, response_text=response_text)
        )

    def finalize_submission(self) -> None:
        """すべての回答が揃った状態で送信を確定する。"""
        if self.status not in (ReportStatus.DELIVERED, ReportStatus.PENDING):
            raise ValueError(f"Cannot finalize submission: current status is {self.status}")
        self.status = ReportStatus.SUBMITTED
        self.submitted_at = datetime.now(UTC)
        self._domain_events.append(
            DailyReportSubmitted(
                report_id=self.report_id,
                member_id=self.member_id,
                project_id=self.project_id,
            )
        )

    def set_ai_summary(self, summary: str) -> None:
        """AI が解析結果のサマリを付与する。"""
        self.ai_summary = summary
        self.status = ReportStatus.ANALYZED
        self.analyzed_at = datetime.now(UTC)

    def unanswered_questions(self) -> list:
        """未回答の質問を返す。"""
        answered_ids = {r.question_id for r in self.responses}
        return [q for q in self.template.questions if q.question_id not in answered_ids]

    def is_fully_answered(self) -> bool:
        return len(self.unanswered_questions()) == 0

    def pop_domain_events(self) -> list:
        events = list(self._domain_events)
        self._domain_events.clear()
        return events

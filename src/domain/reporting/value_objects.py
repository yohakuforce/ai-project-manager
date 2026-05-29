"""Reporting Context の値オブジェクト。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum


@dataclass(frozen=True)
class DailyReportId:
    value: uuid.UUID

    @classmethod
    def generate(cls) -> DailyReportId:
        return cls(value=uuid.uuid4())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class QuestionId:
    value: uuid.UUID

    @classmethod
    def generate(cls) -> QuestionId:
        return cls(value=uuid.uuid4())

    def __str__(self) -> str:
        return str(self.value)


class QuestionType(str, Enum):
    PROGRESS_PERCENT = "progress_percent"
    BLOCKER = "blocker"
    CUSTOMER_PENDING = "customer_pending"
    FREE_TEXT = "free_text"


class ReportStatus(str, Enum):
    PENDING = "pending"  # Template 生成済み、未配信
    DELIVERED = "delivered"  # メンバーへ配信済み
    SUBMITTED = "submitted"  # メンバーが回答送信
    ANALYZED = "analyzed"  # AI が解析完了


@dataclass(frozen=True)
class ReportQuestion:
    """動的日報の個別質問。immutable（一度生成したら変更しない）。"""

    question_id: QuestionId
    question_type: QuestionType
    body: str
    task_id: str | None = None  # 紐づくタスク ID（なければ全体向け質問）


@dataclass(frozen=True)
class ReportTemplate:
    """
    動的日報フォーマット。AI が当日のタスク状態に基づいて生成。
    immutable（変更は新しい Template を生成する）。
    """

    generated_at: datetime
    questions: tuple[ReportQuestion, ...]

    @classmethod
    def create(cls, questions: list[ReportQuestion]) -> ReportTemplate:
        return cls(
            generated_at=datetime.now(UTC),
            questions=tuple(questions),
        )


@dataclass(frozen=True)
class ReportResponse:
    """ReportQuestion への回答。メンバーが入力した値。"""

    question_id: QuestionId
    response_text: str
    responded_at: datetime

    @classmethod
    def create(cls, question_id: QuestionId, response_text: str) -> ReportResponse:
        return cls(
            question_id=question_id,
            response_text=response_text,
            responded_at=datetime.now(UTC),
        )

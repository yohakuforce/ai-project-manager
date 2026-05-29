"""
Project Management Context のエンティティ。
Task / Assignment は Project 集約内で管理する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from .value_objects import (
    AssignmentId,
    AssignmentStatus,
    TaskId,
    TaskPriority,
    TaskSource,
    TaskStatus,
)


@dataclass
class Task:
    """
    作業単位エンティティ。Project 集約内で管理する。
    Context-Hub の extractedTasks（会議抽出）または Issue（Backlog/Redmine）を起点に生成。
    """

    task_id: TaskId
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    source: TaskSource
    source_ref: str | None = None  # 起源 ID（meeting ID / issue ID）
    due_date: date | None = None
    estimated_hours: float | None = None
    dependencies: list[TaskId] = field(default_factory=list)
    ai_confidence: float | None = None  # 0.0〜1.0

    def transition_to(self, new_status: TaskStatus) -> Task:
        """
        ステータス遷移。イミュータブルパターン：新しいオブジェクトを返す。
        不正な遷移は呼び出し元（ドメインサービス）が検証する責務を持つ。
        """
        return Task(
            task_id=self.task_id,
            title=self.title,
            description=self.description,
            status=new_status,
            priority=self.priority,
            source=self.source,
            source_ref=self.source_ref,
            due_date=self.due_date,
            estimated_hours=self.estimated_hours,
            dependencies=list(self.dependencies),
            ai_confidence=self.ai_confidence,
        )

    def update_priority(self, new_priority: TaskPriority) -> Task:
        """優先度更新。イミュータブルパターン。"""
        return Task(
            task_id=self.task_id,
            title=self.title,
            description=self.description,
            status=self.status,
            priority=new_priority,
            source=self.source,
            source_ref=self.source_ref,
            due_date=self.due_date,
            estimated_hours=self.estimated_hours,
            dependencies=list(self.dependencies),
            ai_confidence=self.ai_confidence,
        )

    @property
    def is_overdue(self) -> bool:
        if self.due_date is None:
            return False
        return date.today() > self.due_date and self.status not in (
            TaskStatus.DONE,
            TaskStatus.CANCELLED,
        )


@dataclass
class Assignment:
    """
    Task を Member に割り当てる行為と結果。Project 集約内で管理する。
    AI が DRAFT を作成 → PL/PM が CONFIRMED/REJECTED に変更する2段階承認。
    """

    assignment_id: AssignmentId
    task_id: TaskId
    member_id: str  # MemberId の文字列表現（集約間は ID 参照のみ）
    status: AssignmentStatus
    ai_rationale: str  # AI が割当を提案した根拠（必須）
    confirmed_by: str | None = None  # 承認者のユーザー ID
    confirmed_at: datetime | None = None

    def confirm(self, confirmed_by: str) -> Assignment:
        """割当を確定する。イミュータブルパターン。"""
        return Assignment(
            assignment_id=self.assignment_id,
            task_id=self.task_id,
            member_id=self.member_id,
            status=AssignmentStatus.CONFIRMED,
            ai_rationale=self.ai_rationale,
            confirmed_by=confirmed_by,
            confirmed_at=datetime.now(UTC),
        )

    def reject(self, rejected_by: str) -> Assignment:
        """割当を却下する。イミュータブルパターン。"""
        return Assignment(
            assignment_id=self.assignment_id,
            task_id=self.task_id,
            member_id=self.member_id,
            status=AssignmentStatus.REJECTED,
            ai_rationale=self.ai_rationale,
            confirmed_by=rejected_by,
            confirmed_at=datetime.now(UTC),
        )

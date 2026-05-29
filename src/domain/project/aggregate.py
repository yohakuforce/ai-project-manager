"""
Project 集約ルート。
Project Management Context の中核。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .entities import Assignment, Task
from .value_objects import (
    AssignmentId,
    AssignmentStatus,
    ContextHubProjectRef,
    IssueStatusMapping,
    Phase,
    ProjectId,
    ProjectStatus,
    TaskId,
    TaskSource,
    TaskStatus,
)

# ============================================================
# ドメインイベント（集約内定義）
# ============================================================


@dataclass(frozen=True)
class TaskExtracted:
    task_id: TaskId
    project_id: ProjectId
    source: TaskSource
    source_ref: str | None


@dataclass(frozen=True)
class AssignmentConfirmed:
    assignment_id: AssignmentId
    task_id: TaskId
    member_id: str
    project_id: ProjectId


@dataclass(frozen=True)
class TaskStatusChanged:
    task_id: TaskId
    project_id: ProjectId
    previous_status: TaskStatus
    new_status: TaskStatus


# ============================================================
# Project 集約ルート
# ============================================================


@dataclass
class Project:
    """
    Project 集約ルート。
    Phase / Milestone / Task / Assignment / IssueStatusMapping を保持する。
    """

    project_id: ProjectId
    name: str
    customer: str
    goal: str
    context_hub_ref: ContextHubProjectRef
    phases: list[Phase] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    issue_status_mappings: list[IssueStatusMapping] = field(default_factory=list)
    status: ProjectStatus = ProjectStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # 未発行のドメインイベントを蓄積する。リポジトリが save 後に publish する。
    _domain_events: list = field(default_factory=list, repr=False, compare=False)

    # ============================================================
    # Task 操作
    # ============================================================

    def add_task(self, task: Task) -> None:
        """Task を追加する。同一 task_id の重複は防ぐ。"""
        existing_ids = {t.task_id for t in self.tasks}
        if task.task_id in existing_ids:
            raise ValueError(f"Task {task.task_id} already exists in project {self.project_id}")
        self.tasks.append(task)
        self._domain_events.append(
            TaskExtracted(
                task_id=task.task_id,
                project_id=self.project_id,
                source=task.source,
                source_ref=task.source_ref,
            )
        )
        self.updated_at = datetime.now(UTC)

    def update_task_status(self, task_id: TaskId, new_status: TaskStatus) -> None:
        """Task のステータスを更新する。"""
        for i, task in enumerate(self.tasks):
            if task.task_id == task_id:
                previous_status = task.status
                self.tasks[i] = task.transition_to(new_status)
                self._domain_events.append(
                    TaskStatusChanged(
                        task_id=task_id,
                        project_id=self.project_id,
                        previous_status=previous_status,
                        new_status=new_status,
                    )
                )
                self.updated_at = datetime.now(UTC)
                return
        raise ValueError(f"Task {task_id} not found in project {self.project_id}")

    def find_task(self, task_id: TaskId) -> Task | None:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None

    def active_tasks(self) -> list[Task]:
        """完了・キャンセル以外のタスクを返す。"""
        return [t for t in self.tasks if t.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)]

    # ============================================================
    # Assignment 操作
    # ============================================================

    def add_assignment(self, assignment: Assignment) -> None:
        """AI が生成した割当 DRAFT を追加する。"""
        self.assignments.append(assignment)
        self.updated_at = datetime.now(UTC)

    def confirm_assignment(self, assignment_id: AssignmentId, confirmed_by: str) -> None:
        """PL/PM が割当を確定する。"""
        for i, assignment in enumerate(self.assignments):
            if assignment.assignment_id == assignment_id:
                self.assignments[i] = assignment.confirm(confirmed_by)
                self._domain_events.append(
                    AssignmentConfirmed(
                        assignment_id=assignment_id,
                        task_id=assignment.task_id,
                        member_id=assignment.member_id,
                        project_id=self.project_id,
                    )
                )
                self.updated_at = datetime.now(UTC)
                return
        raise ValueError(f"Assignment {assignment_id} not found in project {self.project_id}")

    def reject_assignment(self, assignment_id: AssignmentId, rejected_by: str) -> None:
        """PL/PM が割当を却下する。"""
        for i, assignment in enumerate(self.assignments):
            if assignment.assignment_id == assignment_id:
                self.assignments[i] = assignment.reject(rejected_by)
                self.updated_at = datetime.now(UTC)
                return
        raise ValueError(f"Assignment {assignment_id} not found in project {self.project_id}")

    def confirmed_assignments(self) -> list[Assignment]:
        return [a for a in self.assignments if a.status == AssignmentStatus.CONFIRMED]

    def draft_assignments(self) -> list[Assignment]:
        return [a for a in self.assignments if a.status == AssignmentStatus.DRAFT]

    # ============================================================
    # IssueStatusMapping 操作
    # ============================================================

    def map_issue_status(self, source_type: str, external_status: str) -> TaskStatus:
        """
        Context-Hub の IssueStatus を AI-PM 内部の TaskStatus に変換する。
        T-011 相互レビュー (3) の対応。
        カスタムマッピングが見つからない場合はデフォルトマッピングを使用。
        """
        for mapping in self.issue_status_mappings:
            if (
                mapping.source_type == source_type
                and mapping.external_status_name.lower() == external_status.lower()
            ):
                return mapping.internal_status
        # デフォルトマッピング（Context-Hub 正規化済みステータス前提）
        DEFAULT_MAPPINGS: dict[str, TaskStatus] = {
            "open": TaskStatus.PENDING,
            "in_progress": TaskStatus.IN_PROGRESS,
            "resolved": TaskStatus.DONE,
            "closed": TaskStatus.DONE,
        }
        normalized = external_status.lower().replace(" ", "_")
        return DEFAULT_MAPPINGS.get(normalized, TaskStatus.PENDING)

    # ============================================================
    # ドメインイベント管理
    # ============================================================

    def pop_domain_events(self) -> list:
        """蓄積されたドメインイベントを取り出してクリアする。"""
        events = list(self._domain_events)
        self._domain_events.clear()
        return events

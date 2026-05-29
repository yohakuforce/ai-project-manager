"""
Project 集約のユニットテスト。
TDD 方針: RED → GREEN → IMPROVE。
"""

from __future__ import annotations

import pytest

from src.domain.project import (
    Assignment,
    AssignmentConfirmed,
    AssignmentId,
    AssignmentStatus,
    ContextHubProjectRef,
    IssueStatusMapping,
    Project,
    ProjectId,
    ProjectStatus,
    Task,
    TaskExtracted,
    TaskId,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskStatusChanged,
)

# ============================================================
# フィクスチャ
# ============================================================


def make_project() -> Project:
    return Project(
        project_id=ProjectId.generate(),
        name="テストプロジェクト",
        customer="テスト顧客",
        goal="テスト目標",
        context_hub_ref=ContextHubProjectRef(
            context_hub_project_id="ctx-001",
            api_endpoint="http://localhost:8000/api/v1",
        ),
    )


def make_task(title: str = "テストタスク") -> Task:
    return Task(
        task_id=TaskId.generate(),
        title=title,
        description="テスト用タスク",
        status=TaskStatus.PENDING,
        priority=TaskPriority.NORMAL,
        source=TaskSource.MANUAL,
    )


def make_assignment(task_id: TaskId, member_id: str = "member-001") -> Assignment:
    return Assignment(
        assignment_id=AssignmentId.generate(),
        task_id=task_id,
        member_id=member_id,
        status=AssignmentStatus.DRAFT,
        ai_rationale="テスト用 AI 根拠",
    )


# ============================================================
# Project 基本操作テスト
# ============================================================


class TestProjectCreation:
    def test_project_has_active_status_by_default(self) -> None:
        project = make_project()
        assert project.status == ProjectStatus.ACTIVE

    def test_project_has_empty_tasks_by_default(self) -> None:
        project = make_project()
        assert project.tasks == []

    def test_project_has_empty_assignments_by_default(self) -> None:
        project = make_project()
        assert project.assignments == []


# ============================================================
# Task 操作テスト
# ============================================================


class TestTaskOperations:
    def test_add_task_appends_to_tasks(self) -> None:
        project = make_project()
        task = make_task()
        project.add_task(task)
        assert len(project.tasks) == 1
        assert project.tasks[0].task_id == task.task_id

    def test_add_task_raises_domain_event(self) -> None:
        project = make_project()
        task = make_task()
        project.add_task(task)
        events = project.pop_domain_events()
        assert len(events) == 1
        assert isinstance(events[0], TaskExtracted)
        assert events[0].task_id == task.task_id

    def test_add_duplicate_task_raises_error(self) -> None:
        project = make_project()
        task = make_task()
        project.add_task(task)
        with pytest.raises(ValueError, match="already exists"):
            project.add_task(task)

    def test_update_task_status_changes_status(self) -> None:
        project = make_project()
        task = make_task()
        project.add_task(task)
        project.pop_domain_events()  # clear TaskExtracted event

        project.update_task_status(task.task_id, TaskStatus.IN_PROGRESS)
        updated = project.find_task(task.task_id)
        assert updated is not None
        assert updated.status == TaskStatus.IN_PROGRESS

    def test_update_task_status_raises_domain_event(self) -> None:
        project = make_project()
        task = make_task()
        project.add_task(task)
        project.pop_domain_events()

        project.update_task_status(task.task_id, TaskStatus.IN_PROGRESS)
        events = project.pop_domain_events()
        assert len(events) == 1
        assert isinstance(events[0], TaskStatusChanged)
        assert events[0].previous_status == TaskStatus.PENDING
        assert events[0].new_status == TaskStatus.IN_PROGRESS

    def test_update_nonexistent_task_raises_error(self) -> None:
        project = make_project()
        with pytest.raises(ValueError, match="not found"):
            project.update_task_status(TaskId.generate(), TaskStatus.IN_PROGRESS)

    def test_active_tasks_excludes_done_and_cancelled(self) -> None:
        project = make_project()
        task1 = make_task("active task")
        task2 = Task(
            task_id=TaskId.generate(),
            title="done task",
            description="",
            status=TaskStatus.DONE,
            priority=TaskPriority.NORMAL,
            source=TaskSource.MANUAL,
        )
        task3 = Task(
            task_id=TaskId.generate(),
            title="cancelled task",
            description="",
            status=TaskStatus.CANCELLED,
            priority=TaskPriority.NORMAL,
            source=TaskSource.MANUAL,
        )
        project.add_task(task1)
        project.add_task(task2)
        project.add_task(task3)

        active = project.active_tasks()
        assert len(active) == 1
        assert active[0].task_id == task1.task_id


# ============================================================
# Assignment 操作テスト
# ============================================================


class TestAssignmentOperations:
    def test_confirm_assignment_changes_status_to_confirmed(self) -> None:
        project = make_project()
        task = make_task()
        project.add_task(task)
        project.pop_domain_events()

        assignment = make_assignment(task.task_id)
        project.add_assignment(assignment)
        project.confirm_assignment(assignment.assignment_id, confirmed_by="pl-user-001")

        confirmed = [a for a in project.assignments if a.assignment_id == assignment.assignment_id]
        assert len(confirmed) == 1
        assert confirmed[0].status == AssignmentStatus.CONFIRMED
        assert confirmed[0].confirmed_by == "pl-user-001"

    def test_confirm_assignment_raises_domain_event(self) -> None:
        project = make_project()
        task = make_task()
        project.add_task(task)
        project.pop_domain_events()

        assignment = make_assignment(task.task_id)
        project.add_assignment(assignment)
        project.confirm_assignment(assignment.assignment_id, confirmed_by="pl-user-001")

        events = project.pop_domain_events()
        assert any(isinstance(e, AssignmentConfirmed) for e in events)

    def test_reject_assignment_changes_status_to_rejected(self) -> None:
        project = make_project()
        task = make_task()
        project.add_task(task)

        assignment = make_assignment(task.task_id)
        project.add_assignment(assignment)
        project.reject_assignment(assignment.assignment_id, rejected_by="pl-user-001")

        rejected = [a for a in project.assignments if a.assignment_id == assignment.assignment_id]
        assert rejected[0].status == AssignmentStatus.REJECTED

    def test_draft_assignments_returns_only_drafts(self) -> None:
        project = make_project()
        task1 = make_task("task1")
        task2 = make_task("task2")
        project.add_task(task1)
        project.add_task(task2)

        a1 = make_assignment(task1.task_id)
        a2 = make_assignment(task2.task_id)
        project.add_assignment(a1)
        project.add_assignment(a2)
        project.confirm_assignment(a1.assignment_id, "pl-user-001")

        drafts = project.draft_assignments()
        assert len(drafts) == 1
        assert drafts[0].assignment_id == a2.assignment_id


# ============================================================
# IssueStatusMapping テスト（T-011 相互レビュー (3)）
# ============================================================


class TestIssueStatusMapping:
    def test_default_mapping_open_to_pending(self) -> None:
        project = make_project()
        result = project.map_issue_status("backlog", "open")
        assert result == TaskStatus.PENDING

    def test_default_mapping_in_progress(self) -> None:
        project = make_project()
        result = project.map_issue_status("backlog", "in_progress")
        assert result == TaskStatus.IN_PROGRESS

    def test_default_mapping_resolved_to_done(self) -> None:
        project = make_project()
        result = project.map_issue_status("redmine", "resolved")
        assert result == TaskStatus.DONE

    def test_default_mapping_closed_to_done(self) -> None:
        project = make_project()
        result = project.map_issue_status("backlog", "closed")
        assert result == TaskStatus.DONE

    def test_custom_mapping_overrides_default(self) -> None:
        project = make_project()
        # カスタムステータス: "対応中" → IN_PROGRESS
        custom_mapping = IssueStatusMapping(
            source_type="backlog",
            external_status_name="in_progress",
            internal_status=TaskStatus.BLOCKED,  # テスト用に BLOCKED にオーバーライド
        )
        project.issue_status_mappings.append(custom_mapping)
        result = project.map_issue_status("backlog", "in_progress")
        assert result == TaskStatus.BLOCKED

    def test_unknown_status_falls_back_to_pending(self) -> None:
        project = make_project()
        result = project.map_issue_status("backlog", "unknown_custom_status")
        assert result == TaskStatus.PENDING

    def test_case_insensitive_matching(self) -> None:
        project = make_project()
        result = project.map_issue_status("backlog", "IN_PROGRESS")
        assert result == TaskStatus.IN_PROGRESS


# ============================================================
# ドメインイベント管理テスト
# ============================================================


class TestDomainEvents:
    def test_pop_domain_events_clears_events(self) -> None:
        project = make_project()
        task = make_task()
        project.add_task(task)

        events1 = project.pop_domain_events()
        events2 = project.pop_domain_events()

        assert len(events1) == 1
        assert len(events2) == 0

"""
PlanService — 会議メモ / Issue から Task を抽出して Project に追加する Application Service。

責務:
  - Context-Hub GET /meetings/{id} の extractedTasks を AI-PM Task エンティティに変換
  - Context-Hub GET /issues から Issue を取り込んで Task を生成
  - LLM アダプタ経由で AI を呼び出し Task 情報を補完（信頼度スコア等）
  - Project リポジトリへの保存
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import date

from src.domain.audit.aggregate import AuditAction, AuditLog
from src.domain.audit.repository import AuditLogRepository
from src.domain.project.entities import Task
from src.domain.project.repository import ProjectRepository
from src.domain.project.value_objects import (
    ProjectId,
    TaskId,
    TaskPriority,
    TaskSource,
    TaskStatus,
)
from src.infrastructure.context_hub.client import ContextHubClient
from src.infrastructure.llm.adapter import LLMAdapter

logger = logging.getLogger(__name__)

_PRIORITY_MAP: dict[str, TaskPriority] = {
    "urgent": TaskPriority.URGENT,
    "high": TaskPriority.HIGH,
    "normal": TaskPriority.NORMAL,
    "low": TaskPriority.LOW,
}


@dataclass(frozen=True)
class ExtractTasksFromMeetingResult:
    """PlanService.extract_tasks_from_meeting の戻り値。"""

    project_id: str
    meeting_id: str
    tasks_added: int
    task_ids: list[str]


@dataclass(frozen=True)
class ImportTasksFromIssuesResult:
    """PlanService.import_tasks_from_issues の戻り値。"""

    project_id: str
    source: str
    tasks_added: int
    task_ids: list[str]


class PlanService:
    """
    Task 抽出・生成 Application Service。
    Context-Hub の会議メモ / Issue を起点に Project に Task を追加する。
    """

    def __init__(
        self,
        project_repository: ProjectRepository,
        context_hub_client: ContextHubClient,
        llm_adapter: LLMAdapter,
        audit_repository: AuditLogRepository | None = None,
    ) -> None:
        self._project_repo = project_repository
        self._hub_client = context_hub_client
        self._llm = llm_adapter
        self._audit_repo = audit_repository

    async def extract_tasks_from_meeting(
        self,
        project_id: str,
        meeting_id: str,
    ) -> ExtractTasksFromMeetingResult:
        """
        会議メモから Task を抽出して Project に追加する。

        1. Project を取得
        2. Context-Hub GET /meetings/{id} で会議データと extractedTasks を取得
        3. LLM で信頼度スコアと補完情報を生成（省略可）
        4. Task エンティティを生成して project.add_task()
        5. Project をリポジトリに保存

        Returns:
            ExtractTasksFromMeetingResult（追加した Task の件数と ID）
        """
        project = await self._project_repo.find_by_id(ProjectId.from_str(project_id))
        if project is None:
            raise ValueError(f"Project が見つかりません: {project_id}")

        context_hub_project_id = project.context_hub_ref.context_hub_project_id
        meeting = await self._hub_client.get_meeting(context_hub_project_id, meeting_id)
        await self._record_audit(
            action=AuditAction.CONTEXT_HUB_QUERIED,
            actor="system",
            project_id=project_id,
            data_ref=f"meeting:{meeting_id}",
        )

        added_ids: list[str] = []
        for extracted in meeting.extracted_tasks:
            task = self._build_task_from_extracted(extracted, meeting_id)
            try:
                project.add_task(task)
                added_ids.append(str(task.task_id))
                await self._record_audit(
                    action=AuditAction.TASK_CREATED,
                    actor="system",
                    project_id=project_id,
                    data_ref=str(task.task_id),
                )
                logger.info(
                    "Task extracted from meeting",
                    extra={
                        "task_id": str(task.task_id),
                        "title": task.title,
                        "meeting_id": meeting_id,
                    },
                )
            except ValueError as exc:
                logger.warning("Task の追加をスキップ: %s", exc)

        await self._project_repo.save(project)

        return ExtractTasksFromMeetingResult(
            project_id=project_id,
            meeting_id=meeting_id,
            tasks_added=len(added_ids),
            task_ids=added_ids,
        )

    async def import_tasks_from_issues(
        self,
        project_id: str,
        source: str,
        status_filter: str = "open",
        updated_since: str | None = None,
    ) -> ImportTasksFromIssuesResult:
        """
        Context-Hub から Issue を取得して Task に変換し Project に追加する。

        Args:
            project_id: 対象プロジェクト ID
            source: Issue ソース（"backlog" | "redmine"）
            status_filter: 取得する Issue ステータス（デフォルト "open"）
            updated_since: ISO 8601 日時。指定すると差分取得になる。

        Returns:
            ImportTasksFromIssuesResult
        """
        project = await self._project_repo.find_by_id(ProjectId.from_str(project_id))
        if project is None:
            raise ValueError(f"Project が見つかりません: {project_id}")

        context_hub_project_id = project.context_hub_ref.context_hub_project_id
        issues = await self._hub_client.get_issues(
            context_hub_project_id,
            source=source,
            status=status_filter,
            updated_since=updated_since,
        )
        await self._record_audit(
            action=AuditAction.CONTEXT_HUB_QUERIED,
            actor="system",
            project_id=project_id,
            data_ref=f"issues:{source}:{status_filter}",
        )

        added_ids: list[str] = []
        for issue in issues:
            internal_status = project.map_issue_status(issue.source_type, issue.status)
            task = self._build_task_from_issue(issue, internal_status)
            try:
                project.add_task(task)
                added_ids.append(str(task.task_id))
                await self._record_audit(
                    action=AuditAction.TASK_CREATED,
                    actor="system",
                    project_id=project_id,
                    data_ref=str(task.task_id),
                )
            except ValueError as exc:
                logger.debug("Issue Task スキップ（重複）: %s", exc)

        await self._project_repo.save(project)

        return ImportTasksFromIssuesResult(
            project_id=project_id,
            source=source,
            tasks_added=len(added_ids),
            task_ids=added_ids,
        )

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    async def _record_audit(
        self,
        *,
        action: AuditAction,
        actor: str,
        project_id: str | None,
        data_ref: str | None,
    ) -> None:
        """監査ログを記録する。失敗してもメインフローは止めない。"""
        if self._audit_repo is None:
            return
        try:
            await self._audit_repo.append(
                AuditLog.create(
                    actor=actor,
                    action=action,
                    project_id=project_id,
                    data_ref=data_ref,
                )
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("監査ログ記録に失敗しました: action=%s error=%s", action, exc)

    @staticmethod
    def _build_task_from_extracted(
        extracted: object,
        meeting_id: str,
    ) -> Task:
        """MeetingExtractedTask → Task エンティティへの変換。"""
        due_date: date | None = None
        if extracted.suggested_due_date:  # type: ignore[union-attr]
            try:
                due_date = date.fromisoformat(extracted.suggested_due_date)  # type: ignore[union-attr]
            except (ValueError, TypeError):
                logger.warning("日付パース失敗: %s", extracted.suggested_due_date)  # type: ignore[union-attr]

        return Task(
            task_id=TaskId.generate(),
            title=extracted.title,  # type: ignore[union-attr]
            description=f"会議から抽出されたタスク。提案担当者: {extracted.suggested_assignee or '未定'}",  # type: ignore[union-attr]
            status=TaskStatus.PENDING,
            priority=TaskPriority.NORMAL,
            source=TaskSource.MEETING_EXTRACTION,
            source_ref=meeting_id,
            due_date=due_date,
            ai_confidence=0.8,
        )

    @staticmethod
    def _build_task_from_issue(issue: object, internal_status: TaskStatus) -> Task:
        """IssueResponse → Task エンティティへの変換。"""
        due_date: date | None = None
        if issue.due_date:  # type: ignore[union-attr]
            with contextlib.suppress(ValueError, TypeError):
                due_date = date.fromisoformat(issue.due_date)  # type: ignore[union-attr]

        priority = _PRIORITY_MAP.get(
            (issue.priority or "normal").lower(),
            TaskPriority.NORMAL,  # type: ignore[union-attr]
        )

        return Task(
            task_id=TaskId.generate(),
            title=issue.title,  # type: ignore[union-attr]
            description=issue.description or "",  # type: ignore[union-attr]
            status=internal_status,
            priority=priority,
            source=TaskSource.ISSUE_IMPORT,
            source_ref=issue.issue_id,  # type: ignore[union-attr]
            due_date=due_date,
            ai_confidence=1.0,  # Issue データは直接マッピング = 信頼度 1.0
        )

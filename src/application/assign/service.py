"""
AssignService — Task × Member 情報から割当案 (DRAFT Assignment) を生成する Application Service。

責務:
  - Project の未割当 Task と Member の Availability / PerformanceHistory を照合
  - LLM アダプタ経由で割当根拠（ai_rationale）を生成
  - Assignment (DRAFT) を Project に追加して保存
  - PL/PM が confirm / reject する前提のため、すべての案は DRAFT で作成
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from src.domain.audit.aggregate import AuditAction, AuditLog
from src.domain.audit.repository import AuditLogRepository
from src.domain.member.aggregate import Member
from src.domain.member.repository import MemberRepository
from src.domain.project.aggregate import Project
from src.domain.project.entities import Assignment, Task
from src.domain.project.repository import ProjectRepository
from src.domain.project.value_objects import (
    AssignmentId,
    AssignmentStatus,
    ProjectId,
    TaskStatus,
)
from src.infrastructure.llm.adapter import LLMAdapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssignDraftResult:
    """AssignService.generate_drafts の戻り値。"""

    project_id: str
    assignments_created: int
    assignment_ids: list[str]
    skipped_task_ids: list[str]  # メンバー候補なし等でスキップされたタスク


@dataclass(frozen=True)
class AssignmentDecisionResult:
    """confirm / reject の戻り値。"""

    project_id: str
    assignment_id: str
    new_status: str


class AssignService:
    """
    Task → Member 割当案生成 Application Service。
    AI が DRAFT 割当案を作成し、PL/PM が確認・承認する。
    """

    def __init__(
        self,
        project_repository: ProjectRepository,
        member_repository: MemberRepository,
        llm_adapter: LLMAdapter,
        audit_repository: AuditLogRepository | None = None,
    ) -> None:
        self._project_repo = project_repository
        self._member_repo = member_repository
        self._llm = llm_adapter
        self._audit_repo = audit_repository

    async def generate_drafts(
        self,
        project_id: str,
        target_date: date | None = None,
    ) -> AssignDraftResult:
        """
        未割当タスクに対して AI が割当案（DRAFT Assignment）を生成する。

        Args:
            project_id: 対象プロジェクト ID
            target_date: 稼働確認の基準日（None の場合は今日）

        Returns:
            AssignDraftResult
        """
        check_date = target_date or date.today()

        project = await self._project_repo.find_by_id(ProjectId.from_str(project_id))
        if project is None:
            raise ValueError(f"Project が見つかりません: {project_id}")

        members = await self._member_repo.find_all()
        if not members:
            logger.warning("メンバーが登録されていません。割当案は生成できません。")
            return AssignDraftResult(
                project_id=project_id,
                assignments_created=0,
                assignment_ids=[],
                skipped_task_ids=[str(t.task_id) for t in project.active_tasks()],
            )

        unassigned_tasks = self._get_unassigned_tasks(project)
        created_ids: list[str] = []
        skipped_ids: list[str] = []

        for task in unassigned_tasks:
            candidate = self._select_best_member(task, members, check_date)
            if candidate is None:
                skipped_ids.append(str(task.task_id))
                continue

            rationale = await self._generate_rationale(task, candidate, check_date)
            assignment = Assignment(
                assignment_id=AssignmentId.generate(),
                task_id=task.task_id,
                member_id=str(candidate.member_id),
                status=AssignmentStatus.DRAFT,
                ai_rationale=rationale,
            )
            project.add_assignment(assignment)
            created_ids.append(str(assignment.assignment_id))
            await self._record_audit(
                action=AuditAction.ASSIGNMENT_CREATED,
                actor="system",
                project_id=project_id,
                data_ref=str(assignment.assignment_id),
            )
            logger.info(
                "Assignment DRAFT 生成",
                extra={
                    "assignment_id": str(assignment.assignment_id),
                    "task_id": str(task.task_id),
                    "member_id": str(candidate.member_id),
                    "member_name": candidate.name,
                },
            )

        await self._project_repo.save(project)

        return AssignDraftResult(
            project_id=project_id,
            assignments_created=len(created_ids),
            assignment_ids=created_ids,
            skipped_task_ids=skipped_ids,
        )

    async def confirm_assignment(
        self,
        project_id: str,
        assignment_id: str,
        confirmed_by: str,
    ) -> AssignmentDecisionResult:
        """PL/PM が割当案を承認する。"""
        project = await self._project_repo.find_by_id(ProjectId.from_str(project_id))
        if project is None:
            raise ValueError(f"Project が見つかりません: {project_id}")

        import uuid as _uuid

        from src.domain.project.value_objects import AssignmentId as AId

        aid = AId(value=_uuid.UUID(assignment_id))
        project.confirm_assignment(aid, confirmed_by)
        await self._project_repo.save(project)
        await self._record_audit(
            action=AuditAction.ASSIGNMENT_CONFIRMED,
            actor=confirmed_by,
            project_id=project_id,
            data_ref=assignment_id,
        )

        return AssignmentDecisionResult(
            project_id=project_id,
            assignment_id=assignment_id,
            new_status=AssignmentStatus.CONFIRMED.value,
        )

    async def reject_assignment(
        self,
        project_id: str,
        assignment_id: str,
        rejected_by: str,
    ) -> AssignmentDecisionResult:
        """PL/PM が割当案を却下する。"""
        project = await self._project_repo.find_by_id(ProjectId.from_str(project_id))
        if project is None:
            raise ValueError(f"Project が見つかりません: {project_id}")

        import uuid as _uuid

        from src.domain.project.value_objects import AssignmentId as AId

        aid = AId(value=_uuid.UUID(assignment_id))
        project.reject_assignment(aid, rejected_by)
        await self._project_repo.save(project)
        await self._record_audit(
            action=AuditAction.ASSIGNMENT_REJECTED,
            actor=rejected_by,
            project_id=project_id,
            data_ref=assignment_id,
        )

        return AssignmentDecisionResult(
            project_id=project_id,
            assignment_id=assignment_id,
            new_status=AssignmentStatus.REJECTED.value,
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
    def _get_unassigned_tasks(project: Project) -> list[Task]:
        """割当済みでない PENDING / BLOCKED の Task を返す。"""
        confirmed_task_ids = {str(a.task_id) for a in project.confirmed_assignments()}
        draft_task_ids = {str(a.task_id) for a in project.draft_assignments()}
        assigned_ids = confirmed_task_ids | draft_task_ids
        return [
            t
            for t in project.active_tasks()
            if t.status in (TaskStatus.PENDING, TaskStatus.BLOCKED)
            and str(t.task_id) not in assigned_ids
        ]

    @staticmethod
    def _select_best_member(
        task: Task,
        members: list[Member],
        check_date: date,
    ) -> Member | None:
        """
        タスクに最も適したメンバーを選択する（シンプルなスコアリング）。

        スコア計算:
          - 稼働時間あり: +2
          - 期限内完了率高い（>= 0.8）: +2
          - 期限内完了率中程度（>= 0.6）: +1
          - スキル登録あり: +1
        """
        best_member: Member | None = None
        best_score = -1

        for member in members:
            available_hours = member.available_hours_on(check_date)
            if available_hours <= 0:
                continue  # その日稼働なし

            score = 2  # 稼働可能ベーススコア
            on_time = member.on_time_rate()
            if on_time >= 0.8:
                score += 2
            elif on_time >= 0.6:
                score += 1

            if member.skills:
                score += 1

            if score > best_score:
                best_score = score
                best_member = member

        return best_member

    async def _generate_rationale(
        self,
        task: Task,
        member: Member,
        check_date: date,
    ) -> str:
        """LLM に割当根拠テキストを生成させる。"""
        available_hours = member.available_hours_on(check_date)
        on_time_rate = member.on_time_rate()
        skill_names = [s.name for s in member.skills[:3]]

        prompt = (
            f"以下のタスクとメンバー情報を基に、割当根拠を日本語 1〜2 文で説明してください。\n\n"
            f"タスク: {task.title}\n"
            f"優先度: {task.priority.value}\n"
            f"期日: {task.due_date or '未設定'}\n\n"
            f"メンバー: {member.name}\n"
            f"役割: {member.role.value}\n"
            f"稼働可能時間({check_date}): {available_hours}時間\n"
            f"過去の期限内完了率: {on_time_rate:.0%}\n"
            f"主なスキル: {', '.join(skill_names) if skill_names else '未登録'}\n\n"
            "割当根拠（1〜2 文）:"
        )
        try:
            response = await self._llm.generate(prompt, max_tokens=200, temperature=0.3)
            return response.content.strip()
        except Exception as exc:
            logger.warning("LLM 呼び出し失敗。デフォルト根拠を使用: %s", exc)
            return (
                f"{member.name}（{member.role.value}）は{check_date}に{available_hours}時間の稼働が可能であり、"
                f"過去の期限内完了率は{on_time_rate:.0%}です。"
            )

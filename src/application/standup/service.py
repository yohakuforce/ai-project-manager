"""
StandupService — 9時のスタンドアップ生成と昨日のアサイン妥当性レビューを司る
Application Service。

責務:
  1. 前日の日報（提出状況・ブロッカー）を集約
  2. 現在のタスク状態（期日超過・ブロック）を把握
  3. Context-Hub から前日更新の課題（出来事）を取得（取得不可ならグレースフルに省略）
  4. 昨日のアサインに問題（過負荷者保有・期日超過・ブロック）がないか確認し、
     問題タスクには別メンバーへの DRAFT 入替案を生成（AssignService 再利用）
  5. スタンドアップ・ナラティブを生成し、リーダーへ共有

最終判断（入替の承認等）はリーダーが行う。本サービスは提案と可視化に留める。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from src.application.assign.service import AssignService
from src.domain.member.repository import MemberRepository
from src.domain.project.aggregate import Project
from src.domain.project.repository import ProjectRepository
from src.domain.project.value_objects import ProjectId, TaskStatus
from src.domain.reporting.repository import DailyReportRepository
from src.domain.reporting.value_objects import QuestionType, ReportStatus
from src.infrastructure.context_hub.client import ContextHubClient
from src.infrastructure.llm.adapter import LLMAdapter
from src.infrastructure.notifiers.protocol import (
    MessageNotification,
    NotificationError,
    Notifier,
)

logger = logging.getLogger(__name__)

# 過負荷とみなす未完了確認済みタスク数の閾値（AlertService と整合）。
_OVERLOAD_TASK_COUNT = 5
# Context-Hub から取得する更新課題のソース。
_ISSUE_SOURCES = ("backlog", "redmine")


@dataclass(frozen=True)
class StandupResult:
    """run の戻り値。"""

    project_id: str
    standup_date: str
    yesterday_submitted: int
    blockers: list[str] = field(default_factory=list)
    problem_task_ids: list[str] = field(default_factory=list)
    reassignments_created: int = 0
    events: list[str] = field(default_factory=list)
    ai_narrative: str = ""
    leader_notified: bool = False


class StandupService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        member_repository: MemberRepository,
        daily_report_repository: DailyReportRepository,
        assign_service: AssignService,
        llm_adapter: LLMAdapter,
        context_hub_client: ContextHubClient | None = None,
        notifier: Notifier | None = None,
        leader_channel: str = "",
    ) -> None:
        self._project_repo = project_repository
        self._member_repo = member_repository
        self._report_repo = daily_report_repository
        self._assign = assign_service
        self._llm = llm_adapter
        self._hub = context_hub_client
        self._notifier = notifier
        self._leader_channel = leader_channel

    async def run(self, project_id: str, standup_date: date | None = None) -> StandupResult:
        today = standup_date or date.today()
        yesterday = today - timedelta(days=1)

        project = await self._project_repo.find_by_id(ProjectId.from_str(project_id))
        if project is None:
            raise ValueError(f"Project が見つかりません: {project_id}")

        # 1. 前日日報
        reports = await self._report_repo.find_by_project_and_date(project_id, yesterday)
        submitted = sum(
            1 for r in reports if r.status in (ReportStatus.SUBMITTED, ReportStatus.ANALYZED)
        )
        blockers = self._collect_blockers(reports)

        # 2-4. アサイン妥当性レビュー → 問題タスク抽出 → DRAFT 入替案
        problem_task_ids = self._detect_problem_tasks(project)
        reassignments_created = 0
        if problem_task_ids:
            reassign = await self._assign.propose_reassignments(project_id, problem_task_ids, today)
            reassignments_created = reassign.reassignments_created

        # 3. Context-Hub の出来事
        events = await self._collect_events(project, yesterday)

        # 5. ナラティブ生成＋リーダー共有
        narrative = await self._generate_narrative(
            project=project,
            today=today,
            submitted=submitted,
            total_members=len(
                await self._member_repo.find_by_project_id(ProjectId.from_str(project_id))
            ),
            blockers=blockers,
            problem_count=len(problem_task_ids),
            reassignments_created=reassignments_created,
            events=events,
        )
        leader_notified = await self._notify_leader(today, narrative)

        return StandupResult(
            project_id=project_id,
            standup_date=today.isoformat(),
            yesterday_submitted=submitted,
            blockers=blockers,
            problem_task_ids=problem_task_ids,
            reassignments_created=reassignments_created,
            events=events,
            ai_narrative=narrative,
            leader_notified=leader_notified,
        )

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_blockers(reports: list) -> list[str]:
        """前日日報の BLOCKER 回答（「なし」以外）を集める。"""
        blockers: list[str] = []
        for report in reports:
            for response in report.responses:
                question = next(
                    (q for q in report.template.questions if q.question_id == response.question_id),
                    None,
                )
                if (
                    question is not None
                    and question.question_type == QuestionType.BLOCKER
                    and response.response_text.strip().lower() not in ("なし", "none", "")
                ):
                    blockers.append(response.response_text.strip())
        return blockers

    def _detect_problem_tasks(self, project: Project) -> list[str]:
        """アサインに問題のあるタスク ID を抽出する。

        問題の定義:
          - 確認済み割当のあるアクティブタスクで、期日超過 または ブロック中
          - 過負荷メンバー（確認済みアクティブタスク数 >= 閾値）が保有するタスク
        """
        confirmed = project.confirmed_assignments()
        active_by_id = {str(t.task_id): t for t in project.active_tasks()}

        # メンバー別の確認済みアクティブタスク
        member_tasks: dict[str, list[str]] = {}
        for a in confirmed:
            if str(a.task_id) in active_by_id:
                member_tasks.setdefault(a.member_id, []).append(str(a.task_id))

        overloaded_members = {
            mid for mid, tids in member_tasks.items() if len(tids) >= _OVERLOAD_TASK_COUNT
        }

        problem_ids: set[str] = set()
        for a in confirmed:
            task = active_by_id.get(str(a.task_id))
            if task is None:
                continue
            if task.is_overdue or task.status == TaskStatus.BLOCKED:
                problem_ids.add(str(task.task_id))
            if a.member_id in overloaded_members:
                problem_ids.add(str(task.task_id))

        return sorted(problem_ids)

    async def _collect_events(self, project: Project, since: date) -> list[str]:
        """Context-Hub から前日更新の課題タイトルを集める。失敗時は空リスト。"""
        if self._hub is None:
            return []
        hub_project_id = project.context_hub_ref.context_hub_project_id
        since_iso = since.isoformat()
        events: list[str] = []
        for source in _ISSUE_SOURCES:
            try:
                issues = await self._hub.get_issues(
                    hub_project_id, source=source, status="open", updated_since=since_iso
                )
            except Exception as exc:  # Context-Hub 不調でスタンドアップを止めない
                logger.warning(
                    "Context-Hub 課題取得に失敗（継続）: source=%s error=%s", source, exc
                )
                continue
            for issue in issues:
                events.append(f"[{source}] {issue.title}（{issue.status}）")
        return events

    async def _generate_narrative(
        self,
        *,
        project: Project,
        today: date,
        submitted: int,
        total_members: int,
        blockers: list[str],
        problem_count: int,
        reassignments_created: int,
        events: list[str],
    ) -> str:
        """スタンドアップ・ナラティブを LLM で生成する。失敗時はテンプレで代替。"""
        task_overdue = sum(1 for t in project.active_tasks() if t.is_overdue)
        task_blocked = sum(1 for t in project.active_tasks() if t.status == TaskStatus.BLOCKED)
        blocker_lines = "\n".join(f"  - {b}" for b in blockers) or "  （なし）"
        event_lines = "\n".join(f"  - {e}" for e in events) or "  （なし）"

        prompt = (
            f"プロジェクト「{project.name}」の {today} 朝スタンドアップを日本語 4〜6 文で生成してください。"
            "リーダー向けに、昨日の振り返り・本日の留意点・アサイン上の懸念を簡潔にまとめます。\n\n"
            f"昨日の日報提出: {submitted}/{total_members}\n"
            f"昨日のブロッカー:\n{blocker_lines}\n"
            f"現在の期日超過タスク: {task_overdue}件 / ブロック中: {task_blocked}件\n"
            f"昨日の更新（Context-Hub）:\n{event_lines}\n"
            f"アサイン上の問題タスク: {problem_count}件（入替 DRAFT 案を {reassignments_created}件 作成）\n\n"
            "スタンドアップ:"
        )
        try:
            response = await self._llm.generate(prompt, max_tokens=500, temperature=0.3)
            return response.content.strip()
        except Exception as exc:
            logger.warning("LLM スタンドアップ生成失敗: %s", exc)
            return (
                f"{today} のスタンドアップ。"
                f"昨日の日報提出 {submitted}/{total_members}、ブロッカー {len(blockers)}件。"
                f"期日超過 {task_overdue}件 / ブロック中 {task_blocked}件。"
                f"アサイン問題タスク {problem_count}件（入替案 {reassignments_created}件を提案）。"
            )

    async def _notify_leader(self, today: date, narrative: str) -> bool:
        if self._notifier is None or not self._leader_channel:
            return False
        payload = MessageNotification(
            channel=self._leader_channel,
            title=f"朝のスタンドアップ（{today.isoformat()}）",
            body=narrative,
            kind="standup",
        )
        try:
            result = await self._notifier.send_message(payload)
        except NotificationError as exc:
            logger.error("スタンドアップ通知に失敗しました: error=%s", exc)
            return False
        return bool(result.success)

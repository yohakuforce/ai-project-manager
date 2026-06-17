"""
WrapUpService — 17:30 の当日総括とリーダー確認ゲート起票を司る Application Service。

フロー:
  run()（17:30 スケジュール起点）
    ├─ 全員提出済み → 総括生成＋リーダー通知＋TASK_STATE_CURRENT ゲート起票
    └─ 未提出あり   → WRAP_UP_DECISION ゲート起票＋リーダーへ「未提出ありますが総括しますか」打診

  run_summary_and_open_gate()（WRAP_UP_DECISION を PROCEED 解決した後に GateService から呼ばれる）
    └─ 総括生成＋リーダー通知＋TASK_STATE_CURRENT ゲート起票

総括内容は OverviewService.generate_daily_summary を再利用し、加えて「注目タスク」
（優先度・期日・遅延で抽出）をリーダーが確認しやすい形で提示する。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from src.application.gate.service import GateService
from src.application.overview.service import OverviewService
from src.domain.gate.aggregate import GateType
from src.domain.member.repository import MemberRepository
from src.domain.project.aggregate import Project
from src.domain.project.entities import Task
from src.domain.project.repository import ProjectRepository
from src.domain.project.value_objects import ProjectId, TaskPriority, TaskStatus
from src.domain.reporting.repository import DailyReportRepository
from src.domain.reporting.value_objects import ReportStatus
from src.infrastructure.notifiers.protocol import (
    MessageNotification,
    NotificationError,
    Notifier,
)

logger = logging.getLogger(__name__)

# 注目タスクとしてリーダーへ提示する最大件数。
_NOTABLE_TASK_LIMIT = 5
# 優先度の並び順（小さいほど先＝注目度高）。
_PRIORITY_RANK: dict[TaskPriority, int] = {
    TaskPriority.URGENT: 0,
    TaskPriority.HIGH: 1,
    TaskPriority.NORMAL: 2,
    TaskPriority.LOW: 3,
}


@dataclass(frozen=True)
class WrapUpResult:
    """run / run_summary_and_open_gate の戻り値。"""

    project_id: str
    wrap_up_date: str
    all_submitted: bool
    gate_type_opened: str | None
    gate_id: str | None
    unsubmitted_member_ids: list[str]


class WrapUpService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        member_repository: MemberRepository,
        daily_report_repository: DailyReportRepository,
        overview_service: OverviewService,
        gate_service: GateService,
        notifier: Notifier | None = None,
        leader_channel: str = "",
    ) -> None:
        self._project_repo = project_repository
        self._member_repo = member_repository
        self._report_repo = daily_report_repository
        self._overview = overview_service
        self._gate = gate_service
        self._notifier = notifier
        self._leader_channel = leader_channel

    async def run(self, project_id: str, wrap_up_date: date | None = None) -> WrapUpResult:
        """17:30 起点。提出状況で分岐する。"""
        target_date = wrap_up_date or date.today()
        unsubmitted_ids = await self._unsubmitted_member_ids(project_id, target_date)

        if not unsubmitted_ids:
            return await self.run_summary_and_open_gate(project_id, target_date)

        # 未提出あり → リーダー判断ゲート
        unsubmitted_names = await self._member_names(project_id, unsubmitted_ids)
        gate = await self._gate.open_gate(
            project_id=project_id,
            gate_type=GateType.WRAP_UP_DECISION,
            gate_date=target_date,
            context={
                "unsubmitted_member_ids": unsubmitted_ids,
                "unsubmitted_names": unsubmitted_names,
            },
        )
        name_lines = "\n".join(f"・{name}" for name in unsubmitted_names)
        await self._notify_leader(
            title=f"未提出 {len(unsubmitted_ids)}名 — 総括を進めますか？（{target_date.isoformat()}）",
            body=(
                "以下のメンバーが日報未提出です。\n"
                f"{name_lines}\n\n"
                "このまま当日総括を進めるか、提出を待つかをご判断ください。"
            ),
            kind="gate",
        )
        return WrapUpResult(
            project_id=project_id,
            wrap_up_date=target_date.isoformat(),
            all_submitted=False,
            gate_type_opened=GateType.WRAP_UP_DECISION.value,
            gate_id=str(gate.gate_id),
            unsubmitted_member_ids=unsubmitted_ids,
        )

    async def run_summary_and_open_gate(
        self, project_id: str, wrap_up_date: date | None = None
    ) -> WrapUpResult:
        """総括を生成してリーダーへ通知し、TASK_STATE_CURRENT ゲートを起票する。"""
        target_date = wrap_up_date or date.today()

        project = await self._project_repo.find_by_id(ProjectId.from_str(project_id))
        if project is None:
            raise ValueError(f"Project が見つかりません: {project_id}")

        summary = await self._overview.generate_daily_summary(project_id, target_date)
        notable = self._select_notable_tasks(project)
        notable_lines = "\n".join(f"・{line}" for line in self._format_notable(notable)) or "・なし"

        gate = await self._gate.open_gate(
            project_id=project_id,
            gate_type=GateType.TASK_STATE_CURRENT,
            gate_date=target_date,
            context={"notable_task_ids": [str(t.task_id) for t in notable]},
        )

        await self._notify_leader(
            title=f"本日の総括（{target_date.isoformat()}）",
            body=(
                f"{summary.ai_narrative}\n\n"
                f"【特に確認したいタスク】\n{notable_lines}\n\n"
                "タスクの進捗状態は最新化されていますか？ ご確認のうえ、最新であれば"
                "「確認済み」として進めてください（確認後、全体ステータス分析と"
                "未割当タスクの割当案生成が走ります）。"
            ),
            kind="gate",
        )

        return WrapUpResult(
            project_id=project_id,
            wrap_up_date=target_date.isoformat(),
            all_submitted=True,
            gate_type_opened=GateType.TASK_STATE_CURRENT.value,
            gate_id=str(gate.gate_id),
            unsubmitted_member_ids=[],
        )

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    async def _unsubmitted_member_ids(self, project_id: str, target_date: date) -> list[str]:
        """当日に提出（SUBMITTED/ANALYZED）が無いメンバー ID を返す。"""
        members = await self._member_repo.find_by_project_id(ProjectId.from_str(project_id))
        reports = await self._report_repo.find_by_project_and_date(project_id, target_date)
        submitted_member_ids = {
            r.member_id
            for r in reports
            if r.status in (ReportStatus.SUBMITTED, ReportStatus.ANALYZED)
        }
        return [str(m.member_id) for m in members if str(m.member_id) not in submitted_member_ids]

    async def _member_names(self, project_id_str: str, member_ids: list[str]) -> list[str]:
        members = await self._member_repo.find_by_project_id(ProjectId.from_str(project_id_str))
        name_by_id = {str(m.member_id): m.name for m in members}
        return [name_by_id.get(mid, mid) for mid in member_ids]

    @staticmethod
    def _select_notable_tasks(project: Project) -> list[Task]:
        """優先度・期日・遅延から「特に確認したいタスク」を抽出する。

        完了/キャンセル以外のアクティブタスクを、(遅延を最優先, 優先度, 期日が近い順)
        で並べ、上位 N 件を返す。
        """

        def sort_key(task: Task) -> tuple:
            overdue_rank = 0 if task.is_overdue else 1
            priority_rank = _PRIORITY_RANK.get(task.priority, 99)
            # 期日なしは後ろへ
            due_ordinal = task.due_date.toordinal() if task.due_date else 10**9
            return (overdue_rank, priority_rank, due_ordinal)

        candidates = [
            t
            for t in project.active_tasks()
            if t.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)
        ]
        return sorted(candidates, key=sort_key)[:_NOTABLE_TASK_LIMIT]

    @staticmethod
    def _format_notable(tasks: list[Task]) -> list[str]:
        lines: list[str] = []
        for t in tasks:
            flags = []
            if t.is_overdue:
                flags.append("期日超過")
            if t.status == TaskStatus.BLOCKED:
                flags.append("ブロック中")
            due = t.due_date.isoformat() if t.due_date else "期日未設定"
            flag_str = f"（{' / '.join(flags)}）" if flags else ""
            lines.append(f"{t.title} [優先度:{t.priority.value} / 期日:{due}]{flag_str}")
        return lines

    async def _notify_leader(self, *, title: str, body: str, kind: str) -> None:
        if self._notifier is None or not self._leader_channel:
            return
        payload = MessageNotification(
            channel=self._leader_channel,
            title=title,
            body=body,
            kind=kind,
        )
        try:
            await self._notifier.send_message(payload)
        except NotificationError as exc:
            logger.error("リーダー通知に失敗しました: title=%s error=%s", title, exc)

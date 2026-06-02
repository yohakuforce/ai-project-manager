"""
ProjectStatusService — リーダーが「タスク状態は最新」と確認した後に発火する
final_analysis を司る Application Service。

責務:
  1. タスク状態・日報提出状況・アクティブアラートを集約（OverviewService 再利用）
  2. プロジェクト全体の健全性（順調 / 要注意）を判定した全体ステータスレポートを生成
  3. 未割当タスクに DRAFT 割当案を生成（AssignService 再利用、不変条件どおり提案のみ）
  4. リーダーへステータスと DRAFT 件数を報告
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from src.application.assign.service import AssignService
from src.application.overview.service import OverviewService
from src.infrastructure.notifiers.protocol import (
    MessageNotification,
    NotificationError,
    Notifier,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FinalAnalysisResult:
    """run_final_analysis の戻り値。"""

    project_id: str
    analysis_date: str
    health: str  # "healthy" | "attention"
    drafts_created: int
    draft_assignment_ids: list[str]
    leader_notified: bool


class ProjectStatusService:
    def __init__(
        self,
        overview_service: OverviewService,
        assign_service: AssignService,
        notifier: Notifier | None = None,
        leader_channel: str = "",
    ) -> None:
        self._overview = overview_service
        self._assign = assign_service
        self._notifier = notifier
        self._leader_channel = leader_channel

    async def run_final_analysis(
        self, project_id: str, analysis_date: date | None = None
    ) -> FinalAnalysisResult:
        """全体ステータス分析と未割当 DRAFT アサインを実行し、リーダーへ報告する。"""
        target_date = analysis_date or date.today()

        summary = await self._overview.generate_daily_summary(project_id, target_date)
        draft_result = await self._assign.generate_drafts(project_id, target_date)

        health = self._judge_health(summary)
        leader_notified = await self._notify_leader(summary, draft_result, health, target_date)

        logger.info(
            "final_analysis 完了",
            extra={
                "project_id": project_id,
                "analysis_date": target_date.isoformat(),
                "health": health,
                "drafts_created": draft_result.assignments_created,
            },
        )

        return FinalAnalysisResult(
            project_id=project_id,
            analysis_date=target_date.isoformat(),
            health=health,
            drafts_created=draft_result.assignments_created,
            draft_assignment_ids=draft_result.assignment_ids,
            leader_notified=leader_notified,
        )

    @staticmethod
    def _judge_health(summary) -> str:
        """期日超過・ブロック・重大アラートがあれば要注意、無ければ順調。"""
        task = summary.task_summary
        has_serious_alert = any(a.severity in ("critical", "high") for a in summary.alert_summary)
        if task.overdue > 0 or task.blocked > 0 or has_serious_alert:
            return "attention"
        return "healthy"

    async def _notify_leader(self, summary, draft_result, health, target_date: date) -> bool:
        if self._notifier is None or not self._leader_channel:
            return False

        health_label = "🟢 順調" if health == "healthy" else "🟡 要注意"
        task = summary.task_summary
        report = summary.report_summary
        alert_lines = (
            "\n".join(f"・{a.category}（{a.severity}）: {a.count}件" for a in summary.alert_summary)
            or "・なし"
        )
        body = (
            f"全体状況: {health_label}\n\n"
            f"{summary.ai_narrative}\n\n"
            f"【タスク】完了 {task.done} / 進行中 {task.in_progress} / "
            f"ブロック {task.blocked} / 期日超過 {task.overdue}（全 {task.total}）\n"
            f"【日報】提出 {report.submitted} / {report.total_members}\n"
            f"【アクティブアラート】\n{alert_lines}\n\n"
            f"【未割当タスクの割当案】DRAFT を {draft_result.assignments_created} 件作成しました。"
            "内容をご確認のうえ承認/却下をお願いします。"
        )
        payload = MessageNotification(
            channel=self._leader_channel,
            title=f"全体ステータスレポート（{target_date.isoformat()}）",
            body=body,
            kind="status",
        )
        try:
            result = await self._notifier.send_message(payload)
        except NotificationError as exc:
            logger.error("ステータスレポート通知に失敗しました: error=%s", exc)
            return False
        return bool(result.success)

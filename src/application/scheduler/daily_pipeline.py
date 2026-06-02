"""
DailyPipelineOrchestrator — 日次パイプラインのステップを正準順に実行する。

責務:
  - アクティブな全プロジェクトに対し、与えられたステップ列を **その順序のまま** 実行する。
  - 1プロジェクト / 1ステップの失敗で全体を止めない（ログして次へ）。これにより
    あるプロジェクトの一時的エラーが他プロジェクトの進行を巻き込まない。
  - べき等性は各サービス側が担保（当日分の日報は二重生成しない等）。本層は順序の
    保証と障害分離に専念する。

順序の保証:
  steps は schedule_plan.build_plan() が正準順に整列して渡す前提。本層では steps を
  並べ替えず、そのまま逐次 await する。report_generate と report_deliver が同一ジョブ
  かつ同一プロジェクトで連続するため、「生成 → 配信」の順序は構造的に壊れない。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from src.application.alert.service import AlertService
from src.application.overview.service import OverviewService
from src.application.track.service import TrackService
from src.domain.project.repository import ProjectRepository

logger = logging.getLogger(__name__)


class DailyPipelineOrchestrator:
    """日次パイプラインを正準順・障害分離つきで実行する Application Service。

    cron に載るステップ（standup / report_generate / report_deliver / report_reminder /
    wrap_up / alert_scan）を扱う。final_analysis はリーダー確認ゲート駆動のため、
    ここ（時刻駆動）では扱わない（GateService の後続処理として ProjectStatusService が走る）。
    """

    # ステップ名 → 実行する非同期処理（projectId を引数に取る）。
    _KNOWN_STEPS = frozenset(
        {
            "standup",
            "report_generate",
            "report_deliver",
            "report_reminder",
            "wrap_up",
            "alert_scan",
        }
    )

    def __init__(
        self,
        project_repository: ProjectRepository,
        track_service: TrackService,
        overview_service: OverviewService,
        alert_service: AlertService,
        standup_service: Any = None,
        wrap_up_service: Any = None,
    ) -> None:
        self._project_repo = project_repository
        self._track = track_service
        self._overview = overview_service
        self._alert = alert_service
        self._standup = standup_service
        self._wrap_up = wrap_up_service

    async def run_steps(self, steps: Sequence[str]) -> None:
        """アクティブな全プロジェクトに対し steps を順番どおりに実行する。

        steps は呼び出し側（build_plan）が正準順に整列済みである前提。本メソッドは
        順序を保持したまま逐次実行する。
        """
        unknown = [s for s in steps if s not in self._KNOWN_STEPS]
        if unknown:
            # 設定ミス由来の未知ステップは無視して既知分だけ進める（落とさない）。
            logger.warning("未知のパイプラインステップを無視します: %s", unknown)

        projects = await self._project_repo.find_all_active()
        if not projects:
            logger.info("アクティブなプロジェクトがありません。パイプラインをスキップします。")
            return

        logger.info("日次パイプライン開始: steps=%s projects=%d", list(steps), len(projects))

        for project in projects:
            project_id = str(project.project_id)
            for step in steps:
                if step not in self._KNOWN_STEPS:
                    continue
                try:
                    await self._run_step(step, project_id)
                except Exception as exc:  # 障害分離が目的（1件の失敗で全体を止めない）
                    logger.error(
                        "パイプラインステップ失敗（継続します）: step=%s project=%s error=%s",
                        step,
                        project_id,
                        exc,
                    )

    async def _run_step(self, step: str, project_id: str) -> None:
        """単一ステップを実行する。"""
        if step == "standup":
            if self._standup is not None:
                await self._standup.run(project_id)
        elif step == "report_generate":
            await self._track.generate_daily_report_templates(project_id)
        elif step == "report_deliver":
            await self._track.deliver_reports(project_id)
        elif step == "report_reminder":
            await self._track.remind_unsubmitted(project_id)
        elif step == "wrap_up":
            if self._wrap_up is not None:
                await self._wrap_up.run(project_id)
        elif step == "alert_scan":
            await self._alert.scan_project(project_id)
        else:  # pragma: no cover - run_steps で除外済み
            raise ValueError(f"未知のステップ: {step}")

"""
PipelineScheduler — APScheduler で日次パイプラインを時刻駆動する Infrastructure 層。

「ユーザー操作でバグらない」ための制御:
  - max_instances=1 + coalesce=True: スキャン間隔を極端に短く設定しても、同一ジョブが
    多重起動・滞留しない（取りこぼしは1回に集約される）。
  - misfire_grace_time: サーバ一時停止後の取りこぼしを猶予内なら拾う。
  - clamp_schedule（build_plan 内）: 不正な時刻/間隔は既定値へ丸めるため、設定ミスでも
    スケジューラは落ちない。
  - ステップ順序は build_plan が正準順に整列して渡すため、ここでは順序を一切変えない。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.application.scheduler.daily_pipeline import DailyPipelineOrchestrator
from src.application.scheduler.schedule_plan import (
    ScheduleConfig,
    ScheduledJob,
    build_plan,
)

logger = logging.getLogger(__name__)

# サーバ停止などで取りこぼした起動を拾う猶予（秒）。
_MISFIRE_GRACE_SECONDS = 600


class PipelineScheduler:
    """日次パイプラインを時刻駆動するスケジューラ。"""

    def __init__(
        self,
        orchestrator: DailyPipelineOrchestrator,
        config: ScheduleConfig,
        timezone: str = "Asia/Tokyo",
    ) -> None:
        self._orchestrator = orchestrator
        self._config = config
        # build_plan は内部で clamp_schedule する（防御的）。
        self._plan: tuple[ScheduledJob, ...] = build_plan(config)
        try:
            self._scheduler = AsyncIOScheduler(timezone=timezone)
        except Exception as exc:  # pragma: no cover - tz 解決失敗時の保険
            logger.warning("timezone=%s の解決に失敗。UTC で起動します: %s", timezone, exc)
            self._scheduler = AsyncIOScheduler()

    @property
    def plan(self) -> tuple[ScheduledJob, ...]:
        """登録予定のジョブ計画（テスト・点検用）。"""
        return self._plan

    def start(self) -> None:
        """ジョブを登録してスケジューラを起動する。"""
        for job in self._plan:
            self._register(job)
        self._scheduler.start()
        logger.info(
            "PipelineScheduler を起動しました: jobs=%s",
            [f"{j.job_id}({j.steps})" for j in self._plan],
        )

    def _register(self, job: ScheduledJob) -> None:
        common = dict(
            id=job.job_id,
            args=[job.steps],
            max_instances=1,
            coalesce=True,
            replace_existing=True,
            misfire_grace_time=_MISFIRE_GRACE_SECONDS,
        )
        if job.trigger == "interval":
            self._scheduler.add_job(
                self._dispatch, "interval", minutes=job.interval_minutes, **common
            )
        else:
            self._scheduler.add_job(
                self._dispatch, "cron", hour=job.hour, minute=job.minute or 0, **common
            )

    async def _dispatch(self, steps: Sequence[str]) -> None:
        """ジョブ本体: オーケストレータへ正準順のステップ列を渡す。"""
        await self._orchestrator.run_steps(steps)

    def shutdown(self) -> None:
        """スケジューラを停止する（多重停止に安全）。"""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

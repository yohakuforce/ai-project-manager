"""
PipelineScheduler のユニットテスト — 計画反映と start/shutdown の安全性。
"""

from __future__ import annotations

from src.application.scheduler.daily_pipeline import DailyPipelineOrchestrator
from src.application.scheduler.schedule_plan import ScheduleConfig
from src.infrastructure.scheduler.pipeline_scheduler import PipelineScheduler


class _EmptyProjectRepo:
    async def find_all_active(self):
        return []


def _orchestrator() -> DailyPipelineOrchestrator:
    # start/shutdown ではジョブは即時発火しないため、各サービスはダミーで良い。
    return DailyPipelineOrchestrator(
        project_repository=_EmptyProjectRepo(),
        track_service=object(),
        overview_service=object(),
        alert_service=object(),
        standup_service=object(),
        wrap_up_service=object(),
    )


class TestPlan:
    def test_plan_reflects_distinct_times(self) -> None:
        sched = PipelineScheduler(_orchestrator(), ScheduleConfig())
        job_ids = {j.job_id for j in sched.plan}
        assert job_ids == {
            "daily-standup",
            "daily-report",
            "daily-reminder",
            "daily-wrap-up",
            "alert-scan",
        }

    def test_plan_collapses_same_time(self) -> None:
        cfg = ScheduleConfig(reminder_hour=17, reminder_minute=0, wrap_up_hour=17, wrap_up_minute=0)
        sched = PipelineScheduler(_orchestrator(), cfg)
        job_ids = {j.job_id for j in sched.plan}
        assert "daily-reminder-wrap-up" in job_ids


class TestStartShutdown:
    async def test_start_registers_jobs_then_shutdown(self) -> None:
        sched = PipelineScheduler(_orchestrator(), ScheduleConfig(), timezone="Asia/Tokyo")
        sched.start()
        try:
            # 内部スケジューラに計画分のジョブ（4 cron + 1 interval）が登録されている
            assert len(sched._scheduler.get_jobs()) == 5
        finally:
            sched.shutdown()

    async def test_double_shutdown_is_safe(self) -> None:
        sched = PipelineScheduler(_orchestrator(), ScheduleConfig(), timezone="UTC")
        sched.start()
        sched.shutdown()
        sched.shutdown()  # 多重停止しても例外を出さない

"""
schedule_plan のユニットテスト — 5フェーズ＋分対応の順序保証と検証ロジック。
"""

from __future__ import annotations

from src.application.scheduler.schedule_plan import (
    CANONICAL_STEP_ORDER,
    DEFAULT_REPORT_HOUR,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_STANDUP_HOUR,
    DEFAULT_WRAP_UP_MINUTE,
    ScheduleConfig,
    build_plan,
    clamp_schedule,
    order_steps,
    validate_schedule,
)


class TestOrderSteps:
    def test_orders_reversed_steps_into_canonical_order(self) -> None:
        reversed_steps = ("report_deliver", "report_generate", "standup")
        result = order_steps(reversed_steps)
        assert result == ("standup", "report_generate", "report_deliver")

    def test_generate_always_precedes_deliver(self) -> None:
        result = order_steps(("report_deliver", "report_generate"))
        assert result.index("report_generate") < result.index("report_deliver")

    def test_full_day_canonical_order(self) -> None:
        result = order_steps(
            ("wrap_up", "report_reminder", "report_deliver", "report_generate", "standup")
        )
        assert result == (
            "standup",
            "report_generate",
            "report_deliver",
            "report_reminder",
            "wrap_up",
        )

    def test_unknown_step_goes_to_end_stably(self) -> None:
        result = order_steps(("alert_scan", "mystery", "standup"))
        assert result[0] == "standup"
        assert result[-1] == "mystery"

    def test_final_analysis_is_not_a_canonical_cron_step(self) -> None:
        # final_analysis はゲート駆動。cron 計画の正準順には含めない。
        assert "final_analysis" not in CANONICAL_STEP_ORDER


class TestValidateSchedule:
    def test_default_schedule_has_no_errors(self) -> None:
        result = validate_schedule(ScheduleConfig())
        assert result.is_valid
        assert result.errors == ()
        assert result.warnings == ()

    def test_out_of_range_hour_produces_error(self) -> None:
        result = validate_schedule(ScheduleConfig(standup_hour=24))
        assert not result.is_valid

    def test_out_of_range_minute_produces_error(self) -> None:
        result = validate_schedule(ScheduleConfig(wrap_up_minute=60))
        assert not result.is_valid

    def test_out_of_range_interval_produces_error(self) -> None:
        assert not validate_schedule(ScheduleConfig(scan_interval_minutes=0)).is_valid
        assert not validate_schedule(ScheduleConfig(scan_interval_minutes=1441)).is_valid

    def test_bool_is_rejected_as_hour(self) -> None:
        assert not validate_schedule(ScheduleConfig(standup_hour=True)).is_valid

    def test_same_time_is_valid_but_warns(self) -> None:
        # reminder と wrap_up を同一時刻に
        cfg = ScheduleConfig(reminder_hour=17, reminder_minute=0, wrap_up_hour=17, wrap_up_minute=0)
        result = validate_schedule(cfg)
        assert result.is_valid
        assert len(result.warnings) == 1
        assert "同じ時刻" in result.warnings[0]


class TestClampSchedule:
    def test_keeps_valid_values(self) -> None:
        cfg = ScheduleConfig(standup_hour=8, wrap_up_hour=18, wrap_up_minute=15)
        assert clamp_schedule(cfg) == cfg

    def test_clamps_invalid_to_defaults(self) -> None:
        cfg = ScheduleConfig(
            standup_hour=99,
            report_hour=-5,
            wrap_up_minute=99,
            scan_interval_minutes=0,
        )
        result = clamp_schedule(cfg)
        assert result.standup_hour == DEFAULT_STANDUP_HOUR
        assert result.report_hour == DEFAULT_REPORT_HOUR
        assert result.wrap_up_minute == DEFAULT_WRAP_UP_MINUTE
        assert result.scan_interval_minutes == DEFAULT_SCAN_INTERVAL_MINUTES


class TestBuildPlan:
    def test_default_distinct_times_produce_five_jobs(self) -> None:
        # standup(9) / report(14) / reminder(17:00) / wrap-up(17:30) / alert-scan
        plan = build_plan(ScheduleConfig())
        job_ids = {j.job_id for j in plan}
        assert job_ids == {
            "daily-standup",
            "daily-report",
            "daily-reminder",
            "daily-wrap-up",
            "alert-scan",
        }

    def test_report_job_keeps_generate_before_deliver(self) -> None:
        plan = build_plan(ScheduleConfig())
        report = next(j for j in plan if j.job_id == "daily-report")
        assert report.steps == ("report_generate", "report_deliver")

    def test_cron_jobs_carry_hour_and_minute(self) -> None:
        plan = build_plan(ScheduleConfig())
        wrap = next(j for j in plan if j.job_id == "daily-wrap-up")
        assert wrap.trigger == "cron"
        assert wrap.hour == 17
        assert wrap.minute == 30

    def test_same_time_collapses_into_single_ordered_job(self) -> None:
        # reminder と wrap-up を同時刻に → 1ジョブに統合され正準順
        cfg = ScheduleConfig(reminder_hour=17, reminder_minute=0, wrap_up_hour=17, wrap_up_minute=0)
        plan = build_plan(cfg)
        job_ids = {j.job_id for j in plan}
        assert "daily-reminder-wrap-up" in job_ids
        combined = next(j for j in plan if j.job_id == "daily-reminder-wrap-up")
        assert combined.steps == ("report_reminder", "wrap_up")

    def test_all_phases_same_time_merge_in_canonical_order(self) -> None:
        cfg = ScheduleConfig(
            standup_hour=10,
            standup_minute=0,
            report_hour=10,
            report_minute=0,
            reminder_hour=10,
            reminder_minute=0,
            wrap_up_hour=10,
            wrap_up_minute=0,
        )
        plan = build_plan(cfg)
        cron_jobs = [j for j in plan if j.trigger == "cron"]
        assert len(cron_jobs) == 1
        assert cron_jobs[0].steps == (
            "standup",
            "report_generate",
            "report_deliver",
            "report_reminder",
            "wrap_up",
        )

    def test_alert_scan_is_interval_job(self) -> None:
        plan = build_plan(ScheduleConfig(scan_interval_minutes=45))
        scan = next(j for j in plan if j.job_id == "alert-scan")
        assert scan.trigger == "interval"
        assert scan.interval_minutes == 45

    def test_invalid_config_is_clamped_in_plan(self) -> None:
        plan = build_plan(ScheduleConfig(standup_hour=99, scan_interval_minutes=99999))
        job_ids = {j.job_id for j in plan}
        assert "alert-scan" in job_ids
        scan = next(j for j in plan if j.job_id == "alert-scan")
        assert scan.interval_minutes == DEFAULT_SCAN_INTERVAL_MINUTES

    def test_every_job_steps_are_canonically_ordered(self) -> None:
        for cfg in (
            ScheduleConfig(),
            ScheduleConfig(reminder_hour=17, reminder_minute=0, wrap_up_hour=17, wrap_up_minute=0),
        ):
            for job in build_plan(cfg):
                indices = [CANONICAL_STEP_ORDER.index(s) for s in job.steps]
                assert indices == sorted(indices)

"""
DailyPipelineOrchestrator のユニットテスト — 順序保持と障害分離。

新しい日次ステップ:
  standup → report_generate → report_deliver → report_reminder → wrap_up → alert_scan
（final_analysis はゲート駆動のため cron 計画＝ここには含まれない）
"""

from __future__ import annotations

from dataclasses import dataclass

from src.application.scheduler.daily_pipeline import DailyPipelineOrchestrator


@dataclass
class _FakeProject:
    project_id: str


class _FakeProjectRepo:
    def __init__(self, project_ids: list[str]) -> None:
        self._projects = [_FakeProject(pid) for pid in project_ids]

    async def find_all_active(self):
        return list(self._projects)


class _RecordingTrack:
    def __init__(self, calls: list[str], fail_on: set[str] | None = None) -> None:
        self._calls = calls
        self._fail_on = fail_on or set()

    async def generate_daily_report_templates(self, project_id: str):
        self._calls.append(f"generate:{project_id}")
        if f"generate:{project_id}" in self._fail_on:
            raise RuntimeError("boom-generate")

    async def deliver_reports(self, project_id: str):
        self._calls.append(f"deliver:{project_id}")

    async def remind_unsubmitted(self, project_id: str):
        self._calls.append(f"reminder:{project_id}")


class _RecordingStandup:
    def __init__(self, calls: list[str], fail_on: set[str] | None = None) -> None:
        self._calls = calls
        self._fail_on = fail_on or set()

    async def run(self, project_id: str):
        self._calls.append(f"standup:{project_id}")
        if f"standup:{project_id}" in self._fail_on:
            raise RuntimeError("boom-standup")


class _RecordingWrapUp:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def run(self, project_id: str):
        self._calls.append(f"wrap_up:{project_id}")


class _RecordingOverview:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def generate_daily_summary(self, project_id: str):  # 互換のため保持（使用されない）
        self._calls.append(f"overview:{project_id}")


class _RecordingAlert:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def scan_project(self, project_id: str):
        self._calls.append(f"scan:{project_id}")


def _build(calls: list[str], project_ids: list[str], **fails):
    return DailyPipelineOrchestrator(
        project_repository=_FakeProjectRepo(project_ids),
        track_service=_RecordingTrack(calls, fails.get("track_fail")),
        overview_service=_RecordingOverview(calls),
        alert_service=_RecordingAlert(calls),
        standup_service=_RecordingStandup(calls, fails.get("standup_fail")),
        wrap_up_service=_RecordingWrapUp(calls),
    )


class TestRunSteps:
    async def test_runs_steps_in_given_order_per_project(self) -> None:
        calls: list[str] = []
        orch = _build(calls, ["p1"])
        await orch.run_steps(("report_generate", "report_deliver"))
        assert calls == ["generate:p1", "deliver:p1"]

    async def test_processes_all_active_projects(self) -> None:
        calls: list[str] = []
        orch = _build(calls, ["p1", "p2"])
        await orch.run_steps(("standup",))
        assert calls == ["standup:p1", "standup:p2"]

    async def test_failure_in_one_step_does_not_stop_others(self) -> None:
        calls: list[str] = []
        # p1 の standup が失敗しても、p1 の後続ステップと p2 は続行
        orch = _build(calls, ["p1", "p2"], standup_fail={"standup:p1"})
        await orch.run_steps(("standup", "alert_scan"))
        assert "standup:p1" in calls
        assert "scan:p1" in calls
        assert "standup:p2" in calls
        assert "scan:p2" in calls

    async def test_no_active_projects_is_noop(self) -> None:
        calls: list[str] = []
        orch = _build(calls, [])
        await orch.run_steps(("standup", "report_generate"))
        assert calls == []

    async def test_unknown_step_is_ignored(self) -> None:
        calls: list[str] = []
        orch = _build(calls, ["p1"])
        await orch.run_steps(("standup", "mystery"))
        assert calls == ["standup:p1"]

    async def test_reminder_and_wrap_up_dispatch(self) -> None:
        calls: list[str] = []
        orch = _build(calls, ["p1"])
        await orch.run_steps(("report_reminder", "wrap_up"))
        assert calls == ["reminder:p1", "wrap_up:p1"]

    async def test_full_canonical_sequence_for_one_project(self) -> None:
        calls: list[str] = []
        orch = _build(calls, ["p1"])
        await orch.run_steps(
            (
                "standup",
                "report_generate",
                "report_deliver",
                "report_reminder",
                "wrap_up",
                "alert_scan",
            )
        )
        assert calls == [
            "standup:p1",
            "generate:p1",
            "deliver:p1",
            "reminder:p1",
            "wrap_up:p1",
            "scan:p1",
        ]

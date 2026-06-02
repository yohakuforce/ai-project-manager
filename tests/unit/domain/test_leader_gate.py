"""LeaderGate 集約のユニットテスト。"""

from __future__ import annotations

from datetime import date

import pytest

from src.domain.gate.aggregate import (
    GateDecision,
    GateError,
    GateStatus,
    GateType,
    LeaderGate,
)


def _gate() -> LeaderGate:
    return LeaderGate.create(
        project_id="project-1",
        gate_type=GateType.TASK_STATE_CURRENT,
        gate_date=date(2026, 6, 2),
        context={"notable_tasks": ["task-1"]},
    )


class TestLeaderGate:
    def test_create_is_pending(self) -> None:
        gate = _gate()
        assert gate.status == GateStatus.PENDING
        assert gate.is_pending is True
        assert gate.decision is None
        assert gate.resolved_at is None
        assert gate.context["notable_tasks"] == ["task-1"]

    def test_resolve_marks_resolved_and_records_decision(self) -> None:
        gate = _gate()
        gate.resolve(decision=GateDecision.PROCEED, resolved_by="leader-1")
        assert gate.status == GateStatus.RESOLVED
        assert gate.is_pending is False
        assert gate.decision == GateDecision.PROCEED
        assert gate.resolved_by == "leader-1"
        assert gate.resolved_at is not None

    def test_double_resolve_raises(self) -> None:
        gate = _gate()
        gate.resolve(decision=GateDecision.SKIP, resolved_by="leader-1")
        with pytest.raises(GateError):
            gate.resolve(decision=GateDecision.PROCEED, resolved_by="leader-2")

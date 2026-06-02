"""GateService のユニットテスト — 起票・一覧・解決と後続ディスパッチ。"""

from __future__ import annotations

from datetime import date

import pytest

from src.application.gate.service import GateService
from src.domain.gate.aggregate import GateDecision, GateStatus, GateType
from src.infrastructure.repositories.in_memory import InMemoryLeaderGateRepository

GATE_DATE = date(2026, 6, 2)


class _Spy:
    """後続コールバックのスパイ。呼び出し引数を記録する。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, date]] = []

    async def __call__(self, project_id: str, gate_date: date) -> None:
        self.calls.append((project_id, gate_date))


class TestOpenAndList:
    async def test_open_gate_persists_pending(self) -> None:
        repo = InMemoryLeaderGateRepository()
        service = GateService(repo)

        gate = await service.open_gate(
            project_id="p1",
            gate_type=GateType.WRAP_UP_DECISION,
            gate_date=GATE_DATE,
            context={"unsubmitted": ["山田"]},
        )

        assert gate.status == GateStatus.PENDING
        pending = await service.list_pending("p1")
        assert len(pending) == 1
        assert pending[0].gate_type == "wrap_up_decision"
        assert pending[0].context["unsubmitted"] == ["山田"]

    async def test_list_pending_excludes_resolved(self) -> None:
        repo = InMemoryLeaderGateRepository()
        service = GateService(repo)
        gate = await service.open_gate(
            project_id="p1", gate_type=GateType.TASK_STATE_CURRENT, gate_date=GATE_DATE
        )
        await service.resolve(str(gate.gate_id), decision=GateDecision.SKIP, resolved_by="leader")
        assert await service.list_pending("p1") == []


class TestResolveDispatch:
    async def test_wrap_up_proceed_triggers_wrap_up_handler(self) -> None:
        repo = InMemoryLeaderGateRepository()
        wrap_up_spy = _Spy()
        final_spy = _Spy()
        service = GateService(
            repo,
            on_wrap_up_proceed=wrap_up_spy,
            on_task_state_confirmed=final_spy,
        )
        gate = await service.open_gate(
            project_id="p1", gate_type=GateType.WRAP_UP_DECISION, gate_date=GATE_DATE
        )

        result = await service.resolve(
            str(gate.gate_id), decision=GateDecision.PROCEED, resolved_by="leader"
        )

        assert result.continuation_ran is True
        assert wrap_up_spy.calls == [("p1", GATE_DATE)]
        assert final_spy.calls == []

    async def test_task_state_proceed_triggers_final_analysis(self) -> None:
        repo = InMemoryLeaderGateRepository()
        wrap_up_spy = _Spy()
        final_spy = _Spy()
        service = GateService(
            repo,
            on_wrap_up_proceed=wrap_up_spy,
            on_task_state_confirmed=final_spy,
        )
        gate = await service.open_gate(
            project_id="p1", gate_type=GateType.TASK_STATE_CURRENT, gate_date=GATE_DATE
        )

        result = await service.resolve(
            str(gate.gate_id), decision=GateDecision.PROCEED, resolved_by="leader"
        )

        assert result.continuation_ran is True
        assert final_spy.calls == [("p1", GATE_DATE)]
        assert wrap_up_spy.calls == []

    async def test_skip_does_not_trigger_continuation(self) -> None:
        repo = InMemoryLeaderGateRepository()
        wrap_up_spy = _Spy()
        service = GateService(repo, on_wrap_up_proceed=wrap_up_spy)
        gate = await service.open_gate(
            project_id="p1", gate_type=GateType.WRAP_UP_DECISION, gate_date=GATE_DATE
        )

        result = await service.resolve(
            str(gate.gate_id), decision=GateDecision.SKIP, resolved_by="leader"
        )

        assert result.continuation_ran is False
        assert wrap_up_spy.calls == []

    async def test_continuation_failure_is_isolated(self) -> None:
        repo = InMemoryLeaderGateRepository()

        async def boom(project_id: str, gate_date: date) -> None:
            raise RuntimeError("downstream failed")

        service = GateService(repo, on_task_state_confirmed=boom)
        gate = await service.open_gate(
            project_id="p1", gate_type=GateType.TASK_STATE_CURRENT, gate_date=GATE_DATE
        )

        result = await service.resolve(
            str(gate.gate_id), decision=GateDecision.PROCEED, resolved_by="leader"
        )

        # 後続が落ちても解決自体は確定し、例外は伝播しない
        assert result.continuation_ran is False
        stored = await repo.find_by_id(gate.gate_id)
        assert stored.status == GateStatus.RESOLVED

    async def test_resolve_unknown_gate_raises(self) -> None:
        import uuid

        service = GateService(InMemoryLeaderGateRepository())
        with pytest.raises(ValueError):
            await service.resolve(
                str(uuid.uuid4()), decision=GateDecision.PROCEED, resolved_by="leader"
            )

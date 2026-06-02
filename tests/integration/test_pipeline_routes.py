"""Pipeline ルーター（ゲート解決 / 手動再実行）の統合テスト。

共有インメモリリポジトリ上で GateService / WrapUpService / ProjectStatusService /
StandupService を実体配線し、API 経由のゲート起票 → 解決 → 後続発火を検証する。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.deps import (
    get_gate_service,
    get_standup_service,
    get_status_service,
    get_wrap_up_service,
)
from src.application.assign.service import AssignService
from src.application.gate.service import GateService
from src.application.overview.service import OverviewService
from src.application.standup.service import StandupService
from src.application.status.service import ProjectStatusService
from src.application.wrapup.service import WrapUpService
from src.domain.member.aggregate import Member
from src.domain.member.value_objects import MemberId, MemberRole
from src.domain.project.aggregate import Project
from src.domain.project.value_objects import ContextHubProjectRef, ProjectId
from src.infrastructure.llm.mock_adapter import MockLLMAdapter
from src.infrastructure.notifiers import InMemoryNotifier
from src.infrastructure.repositories.in_memory import (
    InMemoryAlertRepository,
    InMemoryDailyReportRepository,
    InMemoryLeaderGateRepository,
    InMemoryMemberRepository,
    InMemoryProjectRepository,
)

_API_KEY = "dev-secret-change-in-production"
_HEADERS = {"X-Api-Key": _API_KEY}

# 共有リポジトリ群
_project_repo = InMemoryProjectRepository()
_member_repo = InMemoryMemberRepository()
_alert_repo = InMemoryAlertRepository()
_report_repo = InMemoryDailyReportRepository()
_gate_repo = InMemoryLeaderGateRepository()
_notifier = InMemoryNotifier()
_llm = MockLLMAdapter(fixed_response="テスト要約。")


def _overview() -> OverviewService:
    return OverviewService(_project_repo, _member_repo, _alert_repo, _report_repo, _llm)


def _assign() -> AssignService:
    return AssignService(_project_repo, _member_repo, _llm)


def _wrap_up() -> WrapUpService:
    return WrapUpService(
        project_repository=_project_repo,
        member_repository=_member_repo,
        daily_report_repository=_report_repo,
        overview_service=_overview(),
        gate_service=GateService(_gate_repo),
        notifier=_notifier,
        leader_channel="#leader",
    )


def _status() -> ProjectStatusService:
    return ProjectStatusService(
        overview_service=_overview(),
        assign_service=_assign(),
        notifier=_notifier,
        leader_channel="#leader",
    )


def _standup() -> StandupService:
    return StandupService(
        project_repository=_project_repo,
        member_repository=_member_repo,
        daily_report_repository=_report_repo,
        assign_service=_assign(),
        llm_adapter=_llm,
        context_hub_client=None,
        notifier=_notifier,
        leader_channel="#leader",
    )


def _gate_service() -> GateService:
    gs = GateService(_gate_repo)
    gs.register_continuations(
        on_wrap_up_proceed=_wrap_up().run_summary_and_open_gate,
        on_task_state_confirmed=_status().run_final_analysis,
    )
    return gs


@pytest.fixture(autouse=True)
def _clear():
    _project_repo.clear()
    _member_repo.clear()
    _alert_repo.clear()
    _report_repo.clear()
    _gate_repo.clear()
    _notifier.reset()
    yield


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_gate_service] = _gate_service
    app.dependency_overrides[get_wrap_up_service] = _wrap_up
    app.dependency_overrides[get_status_service] = _status
    app.dependency_overrides[get_standup_service] = _standup
    return TestClient(app)


def _seed_project() -> Project:
    project = Project(
        project_id=ProjectId.generate(),
        name="統合PJ",
        customer="顧客",
        goal="目標",
        context_hub_ref=ContextHubProjectRef(context_hub_project_id="hub", api_endpoint="http://x"),
    )
    asyncio.run(_project_repo.save(project))
    member = Member(
        member_id=MemberId.generate(),
        external_id="ext-1",
        name="担当",
        role=MemberRole.DEVELOPER,
    )
    asyncio.run(_member_repo.save(member))
    return project


class TestPipelineRoutes:
    def test_requires_api_key(self, client: TestClient) -> None:
        resp = client.get("/api/v1/pipeline/p1/gates")
        assert resp.status_code == 401

    def test_wrap_up_then_list_and_resolve_gate(self, client: TestClient) -> None:
        project = _seed_project()
        pid = str(project.project_id)

        # 全員提出済み（メンバーには日報が無い＝未提出だが、ここでは run で WRAP_UP_DECISION）
        # まず手動 wrap-up を実行 → 未提出のため WRAP_UP_DECISION ゲート
        run = client.post("/api/v1/pipeline/wrap-up", headers=_HEADERS, json={"project_id": pid})
        assert run.status_code == 200
        assert run.json()["gate_type_opened"] == "wrap_up_decision"

        # PENDING ゲート一覧
        gates = client.get(f"/api/v1/pipeline/{pid}/gates", headers=_HEADERS)
        assert gates.status_code == 200
        gate_list = gates.json()
        assert len(gate_list) == 1
        gate_id = gate_list[0]["gate_id"]

        # proceed で解決 → 総括継続が走り TASK_STATE_CURRENT ゲートが新たに起票される
        resolve = client.post(
            f"/api/v1/pipeline/{pid}/gates/{gate_id}/resolve",
            headers=_HEADERS,
            json={"decision": "proceed", "resolved_by": "leader-1"},
        )
        assert resolve.status_code == 200
        assert resolve.json()["continuation_ran"] is True

        # 新しい TASK_STATE_CURRENT ゲートが PENDING で存在
        gates2 = client.get(f"/api/v1/pipeline/{pid}/gates", headers=_HEADERS).json()
        assert any(g["gate_type"] == "task_state_current" for g in gates2)

    def test_resolve_task_state_runs_final_analysis(self, client: TestClient) -> None:
        project = _seed_project()
        pid = str(project.project_id)
        # wrap-up summary を直接実行して TASK_STATE_CURRENT ゲートを起票
        gs = _gate_service()  # 同一 _gate_repo を共有
        asyncio.run(_wrap_up().run_summary_and_open_gate(pid))
        pending = asyncio.run(gs.list_pending(pid))
        gate_id = pending[0].gate_id

        resolve = client.post(
            f"/api/v1/pipeline/{pid}/gates/{gate_id}/resolve",
            headers=_HEADERS,
            json={"decision": "proceed", "resolved_by": "leader-1"},
        )
        assert resolve.status_code == 200
        body = resolve.json()
        assert body["gate_type"] == "task_state_current"
        assert body["continuation_ran"] is True
        # final_analysis のステータスレポートがリーダーへ配信される
        assert any(m.payload.kind == "status" for m in _notifier.filter("message"))

    def test_resolve_invalid_decision_returns_422(self, client: TestClient) -> None:
        project = _seed_project()
        pid = str(project.project_id)
        asyncio.run(_wrap_up().run(pid))
        gate_id = asyncio.run(_gate_service().list_pending(pid))[0].gate_id
        resp = client.post(
            f"/api/v1/pipeline/{pid}/gates/{gate_id}/resolve",
            headers=_HEADERS,
            json={"decision": "maybe", "resolved_by": "leader-1"},
        )
        assert resp.status_code == 422

    def test_manual_standup_and_final_analysis(self, client: TestClient) -> None:
        project = _seed_project()
        pid = str(project.project_id)

        standup = client.post(
            "/api/v1/pipeline/standup", headers=_HEADERS, json={"project_id": pid}
        )
        assert standup.status_code == 200
        assert standup.json()["leader_notified"] is True

        final = client.post(
            "/api/v1/pipeline/final-analysis", headers=_HEADERS, json={"project_id": pid}
        )
        assert final.status_code == 200
        assert "health" in final.json()

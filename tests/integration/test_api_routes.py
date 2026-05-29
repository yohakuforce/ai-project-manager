"""
FastAPI ルーターの統合テスト。
インメモリリポジトリ + MockLLM + MockContextHub を使用。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.deps import (
    get_alert_service,
    get_assign_service,
    get_overview_service,
    get_plan_service,
    get_track_service,
)
from src.application.alert.service import AlertService
from src.application.assign.service import AssignService
from src.application.overview.service import OverviewService
from src.application.plan.service import PlanService
from src.application.track.service import TrackService
from src.domain.project.aggregate import Project
from src.domain.project.value_objects import ContextHubProjectRef, ProjectId
from src.infrastructure.context_hub.mock_client import MockContextHubClient
from src.infrastructure.llm.mock_adapter import MockLLMAdapter
from src.infrastructure.repositories.in_memory import (
    InMemoryAlertRepository,
    InMemoryDailyReportRepository,
    InMemoryMemberRepository,
    InMemoryProjectRepository,
)

# テスト用シングルトンリポジトリ
_test_project_repo = InMemoryProjectRepository()
_test_member_repo = InMemoryMemberRepository()
_test_alert_repo = InMemoryAlertRepository()
_test_report_repo = InMemoryDailyReportRepository()
_test_llm = MockLLMAdapter(fixed_response="AIテスト応答")
_test_hub = MockContextHubClient()


# テスト用サービスファクトリ
def _make_plan_service() -> PlanService:
    return PlanService(_test_project_repo, _test_hub, _test_llm)


def _make_assign_service() -> AssignService:
    return AssignService(_test_project_repo, _test_member_repo, _test_llm)


def _make_track_service() -> TrackService:
    return TrackService(_test_project_repo, _test_member_repo, _test_report_repo, _test_llm)


def _make_alert_service() -> AlertService:
    return AlertService(
        _test_project_repo, _test_member_repo, _test_alert_repo, _test_report_repo, _test_llm
    )


def _make_overview_service() -> OverviewService:
    return OverviewService(
        _test_project_repo, _test_member_repo, _test_alert_repo, _test_report_repo, _test_llm
    )


@pytest.fixture(autouse=True)
def clear_repos():
    """各テスト前にリポジトリをクリアする。"""
    _test_project_repo.clear()
    _test_member_repo.clear()
    _test_alert_repo.clear()
    _test_report_repo.clear()
    yield


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_plan_service] = _make_plan_service
    app.dependency_overrides[get_assign_service] = _make_assign_service
    app.dependency_overrides[get_track_service] = _make_track_service
    app.dependency_overrides[get_alert_service] = _make_alert_service
    app.dependency_overrides[get_overview_service] = _make_overview_service
    return TestClient(app)


_API_KEY = "dev-secret-change-in-production"
_HEADERS = {"X-Api-Key": _API_KEY}


def _create_test_project_sync() -> Project:
    """同期版プロジェクト作成（TestClient と同一スレッドで使用）。"""
    import asyncio

    project = Project(
        project_id=ProjectId.generate(),
        name="統合テストPJ",
        customer="テスト顧客",
        goal="統合テスト用プロジェクト",
        context_hub_ref=ContextHubProjectRef(
            context_hub_project_id="hub-test-001",
            api_endpoint="http://localhost:8000/api/v1",
        ),
    )
    asyncio.run(_test_project_repo.save(project))
    return project


class TestHealth:
    def test_health_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestAuthentication:
    def test_requires_api_key(self, client: TestClient) -> None:
        response = client.post("/api/v1/plan/extract-from-meeting", json={})
        assert response.status_code == 401

    def test_rejects_wrong_api_key(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/plan/extract-from-meeting",
            headers={"X-Api-Key": "wrong-key"},
            json={"project_id": "test", "meeting_id": "m1"},
        )
        assert response.status_code == 401


class TestPlanRoutes:
    def test_extract_from_meeting_returns_404_for_unknown_project(self, client: TestClient) -> None:
        import uuid

        response = client.post(
            "/api/v1/plan/extract-from-meeting",
            headers=_HEADERS,
            json={
                "project_id": str(uuid.uuid4()),
                "meeting_id": "meeting-001",
            },
        )
        assert response.status_code == 404

    def test_extract_from_meeting_success(self, client: TestClient) -> None:
        project = _create_test_project_sync()

        response = client.post(
            "/api/v1/plan/extract-from-meeting",
            headers=_HEADERS,
            json={
                "project_id": str(project.project_id),
                "meeting_id": "meeting-001",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tasks_added"] == 2
        assert len(data["task_ids"]) == 2

    def test_import_from_issues_success(self, client: TestClient) -> None:
        project = _create_test_project_sync()

        response = client.post(
            "/api/v1/plan/import-from-issues",
            headers=_HEADERS,
            json={
                "project_id": str(project.project_id),
                "source": "backlog",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tasks_added"] == 1


class TestAssignRoutes:
    def test_generate_drafts_returns_200(self, client: TestClient) -> None:
        project = _create_test_project_sync()

        response = client.post(
            "/api/v1/assign/generate-drafts",
            headers=_HEADERS,
            json={"project_id": str(project.project_id)},
        )
        assert response.status_code == 200
        data = response.json()
        assert "assignments_created" in data


class TestAlertRoutes:
    def test_scan_returns_200(self, client: TestClient) -> None:
        project = _create_test_project_sync()

        response = client.post(
            "/api/v1/alerts/scan",
            headers=_HEADERS,
            json={"project_id": str(project.project_id)},
        )
        assert response.status_code == 200
        data = response.json()
        assert "alerts_created" in data


class TestOverviewRoutes:
    def test_daily_summary_returns_200(self, client: TestClient) -> None:
        project = _create_test_project_sync()

        response = client.post(
            "/api/v1/overview/daily-summary",
            headers=_HEADERS,
            json={"project_id": str(project.project_id)},
        )
        assert response.status_code == 200
        data = response.json()
        assert "task_summary" in data
        assert "ai_narrative" in data

    def test_phase_progress_returns_200(self, client: TestClient) -> None:
        project = _create_test_project_sync()

        response = client.post(
            "/api/v1/overview/phase-progress",
            headers=_HEADERS,
            json={"project_id": str(project.project_id)},
        )
        assert response.status_code == 200
        data = response.json()
        assert "phases" in data

"""登録 GUI（/register）の統合テスト。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import deps
from src.api.app import create_app


@pytest.fixture
def client() -> TestClient:
    # インメモリ DI を新品にしてから検証する
    deps.reset_singletons_for_tests()
    yield TestClient(create_app())
    deps.reset_singletons_for_tests()


class TestRegisterPage:
    def test_get_returns_form(self, client: TestClient) -> None:
        res = client.get("/register")
        assert res.status_code == 200
        body = res.text
        assert "プロジェクトを登録" in body
        assert 'action="/register/project"' in body
        assert 'action="/register/member"' in body

    def test_create_project_then_listed(self, client: TestClient) -> None:
        res = client.post(
            "/register/project",
            data={"name": "案件A", "customer": "顧客A", "goal": "刷新", "context_hub_project_id": "proj-001"},
        )
        assert res.status_code == 200
        assert "登録しました" in res.text
        # 一覧に出る（UUID 付き）
        listed = client.get("/register").text
        assert "案件A" in listed
        assert "proj-001" in listed

    def test_create_project_empty_name_shows_error(self, client: TestClient) -> None:
        res = client.post("/register/project", data={"name": "   "})
        assert res.status_code == 200
        assert "登録エラー" in res.text

    def test_create_member_then_listed(self, client: TestClient) -> None:
        res = client.post(
            "/register/member", data={"external_id": "user-a", "name": "山田", "role": "pm"}
        )
        assert res.status_code == 200
        assert "登録しました" in res.text
        listed = client.get("/register").text
        assert "user-a" in listed
        assert "山田" in listed

    def test_duplicate_member_shows_error(self, client: TestClient) -> None:
        client.post("/register/member", data={"external_id": "dup", "name": "A", "role": "developer"})
        res = client.post(
            "/register/member", data={"external_id": "dup", "name": "B", "role": "developer"}
        )
        assert res.status_code == 200
        assert "登録エラー" in res.text

    def test_settings_and_guide_link_to_register(self, client: TestClient) -> None:
        assert 'href="/register"' in client.get("/settings").text
        assert 'href="/register"' in client.get("/guide").text

    def test_delete_project_removes_it(self, client: TestClient) -> None:
        client.post("/register/project", data={"name": "消す案件"})
        # 一覧から UUID を取り出す
        import re

        body = client.get("/register").text
        assert "消す案件" in body
        m = re.search(r"/register/project/([0-9a-f-]{36})/delete", body)
        assert m, "削除フォームの action が見つからない"
        res = client.post(f"/register/project/{m.group(1)}/delete")
        assert res.status_code == 200
        assert "削除しました" in res.text
        assert "消す案件" not in client.get("/register").text

    def test_delete_member_removes_it(self, client: TestClient) -> None:
        client.post("/register/member", data={"external_id": "del-me", "name": "削除太郎"})
        import re

        body = client.get("/register").text
        m = re.search(r"/register/member/([0-9a-f-]{36})/delete", body)
        assert m
        res = client.post(f"/register/member/{m.group(1)}/delete")
        assert "削除しました" in res.text
        assert "削除太郎" not in client.get("/register").text

    def test_delete_unknown_project_shows_error(self, client: TestClient) -> None:
        import uuid

        res = client.post(f"/register/project/{uuid.uuid4()}/delete")
        assert res.status_code == 200
        assert "削除エラー" in res.text

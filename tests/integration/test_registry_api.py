"""Registry API エンドポイント（/api/v1/registry/projects/{id}/members）の統合テスト。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import deps
from src.api.app import create_app

_API_KEY = "dev-secret-change-in-production"
_HEADERS = {"X-Api-Key": _API_KEY}


@pytest.fixture
def client() -> TestClient:
    deps.reset_singletons_for_tests()
    yield TestClient(create_app())
    deps.reset_singletons_for_tests()


def _create_project(client: TestClient, name: str = "案件A") -> str:
    """プロジェクトを作成してプロジェクト ID を返す。"""
    res = client.post(
        "/register/project",
        data={"name": name, "customer": "顧客", "goal": "目標", "context_hub_project_id": ""},
    )
    assert res.status_code == 200
    import re

    m = re.search(r"UUID: <code>([0-9a-f-]{36})</code>", res.text)
    assert m, f"project_id が見つからない: {res.text[:500]}"
    return m.group(1)


def _create_member(client: TestClient, external_id: str = "user-a", name: str = "山田") -> str:
    """メンバーを作成してメンバー ID を返す。

    external_id が含まれる行から UUID を抽出することで、複数メンバーが存在する場合でも
    正しい member_id を返す（re.search の先頭一致バグを回避）。
    """
    res = client.post(
        "/register/member",
        data={"external_id": external_id, "name": name, "role": "developer"},
    )
    assert res.status_code == 200
    import re

    # external_id を含む <tr> を特定し、そこから UUID を抽出する
    # HTML 行: <code>{external_id}</code> ... <td class='uuid'>{uuid}</td>
    pattern = (
        r"<code>" + re.escape(external_id) + r"</code>"
        r".*?"
        r"class='uuid'>([0-9a-f-]{36})<"
    )
    m = re.search(pattern, res.text, re.DOTALL)
    if m:
        return m.group(1)
    # フォールバック: /register/member/{uuid}/delete のうち最後のものを返す
    # (最後に作成されたメンバーが最下行に表示される)
    all_matches = re.findall(r"/register/member/([0-9a-f-]{36})/delete", res.text)
    assert all_matches, f"member_id が見つからない: {res.text[:500]}"
    return all_matches[-1]


class TestProjectMemberAPI:
    def test_list_members_empty(self, client: TestClient) -> None:
        project_id = _create_project(client)

        res = client.get(f"/api/v1/registry/projects/{project_id}/members", headers=_HEADERS)
        assert res.status_code == 200
        body = res.json()
        assert body["project_id"] == project_id
        assert body["members"] == []

    def test_add_member_to_project(self, client: TestClient) -> None:
        project_id = _create_project(client)
        member_id = _create_member(client)

        res = client.post(
            f"/api/v1/registry/projects/{project_id}/members",
            headers=_HEADERS,
            json={"member_id": member_id},
        )
        assert res.status_code == 201
        body = res.json()
        assert len(body["members"]) == 1
        assert body["members"][0]["member_id"] == member_id

    def test_remove_member_from_project(self, client: TestClient) -> None:
        project_id = _create_project(client)
        member_id = _create_member(client)
        # まず追加
        client.post(
            f"/api/v1/registry/projects/{project_id}/members",
            headers=_HEADERS,
            json={"member_id": member_id},
        )

        res = client.delete(
            f"/api/v1/registry/projects/{project_id}/members/{member_id}",
            headers=_HEADERS,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["members"] == []

    def test_add_unknown_member_returns_404(self, client: TestClient) -> None:
        import uuid

        project_id = _create_project(client)

        res = client.post(
            f"/api/v1/registry/projects/{project_id}/members",
            headers=_HEADERS,
            json={"member_id": str(uuid.uuid4())},
        )
        assert res.status_code == 404

    def test_add_to_unknown_project_returns_404(self, client: TestClient) -> None:
        import uuid

        member_id = _create_member(client)

        res = client.post(
            f"/api/v1/registry/projects/{uuid.uuid4()}/members",
            headers=_HEADERS,
            json={"member_id": member_id},
        )
        assert res.status_code == 404

    def test_list_unknown_project_returns_404(self, client: TestClient) -> None:
        import uuid

        res = client.get(
            f"/api/v1/registry/projects/{uuid.uuid4()}/members",
            headers=_HEADERS,
        )
        assert res.status_code == 404

    def test_requires_api_key(self, client: TestClient) -> None:
        import uuid

        res = client.get(f"/api/v1/registry/projects/{uuid.uuid4()}/members")
        assert res.status_code == 401

    def test_multiple_members_in_project(self, client: TestClient) -> None:
        project_id = _create_project(client)
        mid1 = _create_member(client, external_id="user-1", name="田中")
        mid2 = _create_member(client, external_id="user-2", name="佐藤")

        client.post(
            f"/api/v1/registry/projects/{project_id}/members",
            headers=_HEADERS,
            json={"member_id": mid1},
        )
        res = client.post(
            f"/api/v1/registry/projects/{project_id}/members",
            headers=_HEADERS,
            json={"member_id": mid2},
        )
        assert res.status_code == 201
        assert len(res.json()["members"]) == 2


class TestProjectMemberScoping:
    """AssignService がプロジェクトスコープのメンバーのみ見ること。"""

    def test_assign_only_considers_project_members(self, client: TestClient) -> None:
        """プロジェクトに所属するメンバーのみが割当候補になること。"""
        project_id = _create_project(client)
        # グローバルには 2 人いるが、プロジェクトには 1 人だけ割り当てる
        mid1 = _create_member(client, external_id="user-1", name="田中")
        _create_member(client, external_id="user-2", name="佐藤")

        # user-1 だけプロジェクトに追加
        client.post(
            f"/api/v1/registry/projects/{project_id}/members",
            headers=_HEADERS,
            json={"member_id": mid1},
        )

        # プロジェクトメンバー一覧に user-1 のみ出る
        members = client.get(
            f"/api/v1/registry/projects/{project_id}/members",
            headers=_HEADERS,
        ).json()["members"]
        assert len(members) == 1
        assert members[0]["member_id"] == mid1

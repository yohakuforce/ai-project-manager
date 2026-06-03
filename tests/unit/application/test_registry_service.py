"""RegistryService のユニットテスト。"""

from __future__ import annotations

import pytest

from src.application.registry.service import RegistryError, RegistryService
from src.infrastructure.repositories.in_memory import (
    InMemoryMemberRepository,
    InMemoryProjectRepository,
)

pytestmark = pytest.mark.asyncio


def _service():
    return RegistryService(InMemoryProjectRepository(), InMemoryMemberRepository())


class TestProjects:
    async def test_create_and_list(self):
        svc = _service()
        view = await svc.create_project(
            name="案件A",
            customer="顧客A",
            goal="刷新",
            context_hub_project_id="proj-001",
            api_endpoint="http://localhost:8000/api/v1",
        )
        assert view.name == "案件A"
        assert view.project_id  # UUID 文字列
        listed = await svc.list_projects()
        assert len(listed) == 1
        assert listed[0].context_hub_project_id == "proj-001"

    async def test_empty_name_rejected(self):
        svc = _service()
        with pytest.raises(RegistryError):
            await svc.create_project(
                name="   ", customer="", goal="", context_hub_project_id="", api_endpoint=""
            )

    async def test_delete_project(self):
        svc = _service()
        view = await svc.create_project(
            name="案件A", customer="", goal="", context_hub_project_id="", api_endpoint=""
        )
        name = await svc.delete_project(view.project_id)
        assert name == "案件A"
        assert await svc.list_projects() == []

    async def test_delete_unknown_project_rejected(self):
        import uuid

        svc = _service()
        with pytest.raises(RegistryError):
            await svc.delete_project(str(uuid.uuid4()))

    async def test_delete_invalid_id_rejected(self):
        svc = _service()
        with pytest.raises(RegistryError):
            await svc.delete_project("not-a-uuid")


class TestMembers:
    async def test_create_and_list(self):
        svc = _service()
        await svc.create_member(external_id="user-a", name="山田", role="developer")
        members = await svc.list_members()
        assert len(members) == 1
        assert members[0].external_id == "user-a"
        assert members[0].role == "developer"

    async def test_duplicate_external_id_rejected(self):
        svc = _service()
        await svc.create_member(external_id="user-a", name="山田", role="developer")
        with pytest.raises(RegistryError):
            await svc.create_member(external_id="user-a", name="別人", role="pm")

    async def test_invalid_role_rejected(self):
        svc = _service()
        with pytest.raises(RegistryError):
            await svc.create_member(external_id="user-b", name="鈴木", role="wizard")

    async def test_missing_fields_rejected(self):
        svc = _service()
        with pytest.raises(RegistryError):
            await svc.create_member(external_id="", name="名無し", role="developer")

    async def test_delete_member(self):
        svc = _service()
        view = await svc.create_member(external_id="user-a", name="山田", role="developer")
        name = await svc.delete_member(view.member_id)
        assert name == "山田"
        assert await svc.list_members() == []
        # external_id が解放され、同じIDで再登録できる
        await svc.create_member(external_id="user-a", name="山田2", role="pm")
        assert len(await svc.list_members()) == 1

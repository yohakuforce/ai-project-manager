"""RegistryService — プロジェクトメンバーシップ管理のユニットテスト。"""

from __future__ import annotations

import pytest

from src.application.registry.service import RegistryError, RegistryService
from src.infrastructure.repositories.in_memory import (
    InMemoryMemberRepository,
    InMemoryProjectMemberRepository,
    InMemoryProjectRepository,
)

pytestmark = pytest.mark.asyncio


def _service() -> RegistryService:
    member_repo = InMemoryMemberRepository()
    pm_repo = InMemoryProjectMemberRepository(member_repo)
    return RegistryService(
        project_repository=InMemoryProjectRepository(),
        member_repository=member_repo,
        project_member_repository=pm_repo,
    )


class TestProjectMembership:
    async def test_add_member_to_project(self) -> None:
        svc = _service()
        project = await svc.create_project(
            name="案件A", customer="", goal="", context_hub_project_id="", api_endpoint=""
        )
        member = await svc.create_member(external_id="user-a", name="山田", role="developer")

        await svc.add_member_to_project(project.project_id, member.member_id)

        members = await svc.list_project_members(project.project_id)
        assert len(members) == 1
        assert members[0].member_id == member.member_id

    async def test_add_member_idempotent(self) -> None:
        """同じメンバーを 2 回追加しても重複しない。"""
        svc = _service()
        project = await svc.create_project(
            name="案件A", customer="", goal="", context_hub_project_id="", api_endpoint=""
        )
        member = await svc.create_member(external_id="user-a", name="山田", role="developer")

        await svc.add_member_to_project(project.project_id, member.member_id)
        await svc.add_member_to_project(project.project_id, member.member_id)

        members = await svc.list_project_members(project.project_id)
        assert len(members) == 1

    async def test_remove_member_from_project(self) -> None:
        svc = _service()
        project = await svc.create_project(
            name="案件A", customer="", goal="", context_hub_project_id="", api_endpoint=""
        )
        member = await svc.create_member(external_id="user-a", name="山田", role="developer")
        await svc.add_member_to_project(project.project_id, member.member_id)

        await svc.remove_member_from_project(project.project_id, member.member_id)

        members = await svc.list_project_members(project.project_id)
        assert len(members) == 0

    async def test_list_project_members_empty(self) -> None:
        svc = _service()
        project = await svc.create_project(
            name="案件A", customer="", goal="", context_hub_project_id="", api_endpoint=""
        )
        members = await svc.list_project_members(project.project_id)
        assert members == []

    async def test_list_project_members_scoped_per_project(self) -> None:
        """2 プロジェクトのメンバーが互いに見えないこと。"""
        svc = _service()
        pj1 = await svc.create_project(
            name="PJ1", customer="", goal="", context_hub_project_id="", api_endpoint=""
        )
        pj2 = await svc.create_project(
            name="PJ2", customer="", goal="", context_hub_project_id="", api_endpoint=""
        )
        m1 = await svc.create_member(external_id="user-1", name="田中", role="developer")
        m2 = await svc.create_member(external_id="user-2", name="佐藤", role="pm")

        await svc.add_member_to_project(pj1.project_id, m1.member_id)
        await svc.add_member_to_project(pj2.project_id, m2.member_id)

        pj1_members = await svc.list_project_members(pj1.project_id)
        pj2_members = await svc.list_project_members(pj2.project_id)

        assert len(pj1_members) == 1
        assert pj1_members[0].member_id == m1.member_id
        assert len(pj2_members) == 1
        assert pj2_members[0].member_id == m2.member_id

    async def test_add_unknown_project_raises_error(self) -> None:
        import uuid

        svc = _service()
        member = await svc.create_member(external_id="user-a", name="山田", role="developer")
        with pytest.raises(RegistryError, match="プロジェクト"):
            await svc.add_member_to_project(str(uuid.uuid4()), member.member_id)

    async def test_add_unknown_member_raises_error(self) -> None:
        import uuid

        svc = _service()
        project = await svc.create_project(
            name="案件A", customer="", goal="", context_hub_project_id="", api_endpoint=""
        )
        with pytest.raises(RegistryError, match="メンバー"):
            await svc.add_member_to_project(project.project_id, str(uuid.uuid4()))

    async def test_add_invalid_project_id_raises_error(self) -> None:
        svc = _service()
        with pytest.raises(RegistryError, match="不正なプロジェクトID"):
            await svc.add_member_to_project("not-a-uuid", "also-not-a-uuid")

    async def test_list_unknown_project_raises_error(self) -> None:
        import uuid

        svc = _service()
        with pytest.raises(RegistryError, match="プロジェクト"):
            await svc.list_project_members(str(uuid.uuid4()))

    async def test_no_pm_repo_raises_registry_error(self) -> None:
        """project_member_repository が None のとき RegistryError が発生すること。"""
        svc = RegistryService(
            project_repository=InMemoryProjectRepository(),
            member_repository=InMemoryMemberRepository(),
            project_member_repository=None,
        )
        project = await svc.create_project(
            name="案件A", customer="", goal="", context_hub_project_id="", api_endpoint=""
        )
        with pytest.raises(RegistryError, match="ProjectMemberRepository"):
            await svc.add_member_to_project(project.project_id, "dummy-id")

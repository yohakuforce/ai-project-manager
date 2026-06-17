"""InMemoryProjectMemberRepository と find_by_project_id のユニットテスト。"""

from __future__ import annotations

import pytest

from src.domain.member.aggregate import Member
from src.domain.member.value_objects import MemberId, MemberRole
from src.domain.project.value_objects import ProjectId
from src.infrastructure.repositories.in_memory import (
    InMemoryMemberRepository,
    InMemoryProjectMemberRepository,
)

pytestmark = pytest.mark.asyncio


def _member(name: str) -> Member:
    return Member(
        member_id=MemberId.generate(),
        external_id=f"ext-{name}",
        name=name,
        role=MemberRole.DEVELOPER,
    )


class TestInMemoryProjectMemberRepository:
    async def test_add_and_list(self) -> None:
        member_repo = InMemoryMemberRepository()
        pm_repo = InMemoryProjectMemberRepository(member_repo)
        pid = ProjectId.generate()
        m = _member("田中")
        await member_repo.save(m)

        await pm_repo.add(pid, m.member_id)

        ids = await pm_repo.list_member_ids(pid)
        assert str(m.member_id) in ids

    async def test_remove(self) -> None:
        member_repo = InMemoryMemberRepository()
        pm_repo = InMemoryProjectMemberRepository(member_repo)
        pid = ProjectId.generate()
        m = _member("田中")
        await member_repo.save(m)
        await pm_repo.add(pid, m.member_id)

        await pm_repo.remove(pid, m.member_id)

        ids = await pm_repo.list_member_ids(pid)
        assert str(m.member_id) not in ids

    async def test_remove_nonexistent_is_no_op(self) -> None:
        member_repo = InMemoryMemberRepository()
        pm_repo = InMemoryProjectMemberRepository(member_repo)
        pid = ProjectId.generate()
        mid = MemberId.generate()
        # should not raise
        await pm_repo.remove(pid, mid)

    async def test_add_idempotent(self) -> None:
        member_repo = InMemoryMemberRepository()
        pm_repo = InMemoryProjectMemberRepository(member_repo)
        pid = ProjectId.generate()
        m = _member("田中")
        await member_repo.save(m)

        await pm_repo.add(pid, m.member_id)
        await pm_repo.add(pid, m.member_id)

        ids = await pm_repo.list_member_ids(pid)
        assert ids.count(str(m.member_id)) == 1

    async def test_scoped_to_project(self) -> None:
        member_repo = InMemoryMemberRepository()
        pm_repo = InMemoryProjectMemberRepository(member_repo)
        pid1 = ProjectId.generate()
        pid2 = ProjectId.generate()
        m1 = _member("田中")
        m2 = _member("佐藤")
        await member_repo.save(m1)
        await member_repo.save(m2)

        await pm_repo.add(pid1, m1.member_id)
        await pm_repo.add(pid2, m2.member_id)

        assert str(m1.member_id) not in await pm_repo.list_member_ids(pid2)
        assert str(m2.member_id) not in await pm_repo.list_member_ids(pid1)


class TestInMemoryMemberFindByProject:
    async def test_find_by_project_returns_linked_members(self) -> None:
        member_repo = InMemoryMemberRepository()
        pm_repo = InMemoryProjectMemberRepository(member_repo)
        pid = ProjectId.generate()
        m1 = _member("田中")
        m2 = _member("佐藤")
        await member_repo.save(m1)
        await member_repo.save(m2)
        await pm_repo.add(pid, m1.member_id)

        result = await member_repo.find_by_project_id(pid)

        assert len(result) == 1
        assert result[0].member_id == m1.member_id

    async def test_find_by_project_empty_when_no_link(self) -> None:
        member_repo = InMemoryMemberRepository()
        pid = ProjectId.generate()
        m = _member("田中")
        await member_repo.save(m)

        result = await member_repo.find_by_project_id(pid)

        assert result == []

    async def test_clear_resets_memberships(self) -> None:
        member_repo = InMemoryMemberRepository()
        pm_repo = InMemoryProjectMemberRepository(member_repo)
        pid = ProjectId.generate()
        m = _member("田中")
        await member_repo.save(m)
        await pm_repo.add(pid, m.member_id)

        member_repo.clear()

        result = await member_repo.find_by_project_id(pid)
        assert result == []

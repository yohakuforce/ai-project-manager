"""SqlAlchemy ProjectMemberRepository と find_by_project_id の統合テスト。

aiosqlite（インメモリ）でテーブルを建てて検証する。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.member.aggregate import Member
from src.domain.member.value_objects import MemberId, MemberRole
from src.domain.project.aggregate import Project
from src.domain.project.value_objects import ContextHubProjectRef, ProjectId
from src.infrastructure.db.base import Base
from src.infrastructure.repositories.sqlalchemy import (
    SqlAlchemyMemberRepository,
    SqlAlchemyProjectMemberRepository,
    SqlAlchemyProjectRepository,
)


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _member(name: str) -> Member:
    return Member(
        member_id=MemberId.generate(),
        external_id=f"ext-{name}",
        name=name,
        role=MemberRole.DEVELOPER,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _project(name: str = "テストPJ") -> Project:
    return Project(
        project_id=ProjectId.generate(),
        name=name,
        customer="顧客",
        goal="目標",
        context_hub_ref=ContextHubProjectRef(
            context_hub_project_id="hub-001",
            api_endpoint="http://localhost:8000",
        ),
    )


class TestSqlAlchemyProjectMemberRepository:
    async def test_add_and_list(self, session_factory) -> None:
        project_repo = SqlAlchemyProjectRepository(session_factory)
        member_repo = SqlAlchemyMemberRepository(session_factory)
        pm_repo = SqlAlchemyProjectMemberRepository(session_factory)

        project = _project()
        member = _member("田中")
        await project_repo.save(project)
        await member_repo.save(member)

        await pm_repo.add(project.project_id, member.member_id)

        ids = await pm_repo.list_member_ids(project.project_id)
        assert str(member.member_id) in ids

    async def test_add_idempotent(self, session_factory) -> None:
        """同じメンバーを 2 回追加しても UNIQUE 制約違反にならない。"""
        project_repo = SqlAlchemyProjectRepository(session_factory)
        member_repo = SqlAlchemyMemberRepository(session_factory)
        pm_repo = SqlAlchemyProjectMemberRepository(session_factory)

        project = _project()
        member = _member("田中")
        await project_repo.save(project)
        await member_repo.save(member)

        await pm_repo.add(project.project_id, member.member_id)
        await pm_repo.add(project.project_id, member.member_id)

        ids = await pm_repo.list_member_ids(project.project_id)
        assert ids.count(str(member.member_id)) == 1

    async def test_remove(self, session_factory) -> None:
        project_repo = SqlAlchemyProjectRepository(session_factory)
        member_repo = SqlAlchemyMemberRepository(session_factory)
        pm_repo = SqlAlchemyProjectMemberRepository(session_factory)

        project = _project()
        member = _member("田中")
        await project_repo.save(project)
        await member_repo.save(member)
        await pm_repo.add(project.project_id, member.member_id)

        await pm_repo.remove(project.project_id, member.member_id)

        ids = await pm_repo.list_member_ids(project.project_id)
        assert str(member.member_id) not in ids

    async def test_find_by_project_id(self, session_factory) -> None:
        project_repo = SqlAlchemyProjectRepository(session_factory)
        member_repo = SqlAlchemyMemberRepository(session_factory)
        pm_repo = SqlAlchemyProjectMemberRepository(session_factory)

        project = _project()
        m1 = _member("田中")
        m2 = _member("佐藤")
        await project_repo.save(project)
        await member_repo.save(m1)
        await member_repo.save(m2)
        await pm_repo.add(project.project_id, m1.member_id)

        result = await member_repo.find_by_project_id(project.project_id)

        assert len(result) == 1
        assert result[0].member_id == m1.member_id

    async def test_cascade_delete_on_project(self, session_factory) -> None:
        """プロジェクト削除時に project_members も消えること。"""
        project_repo = SqlAlchemyProjectRepository(session_factory)
        member_repo = SqlAlchemyMemberRepository(session_factory)
        pm_repo = SqlAlchemyProjectMemberRepository(session_factory)

        project = _project()
        member = _member("田中")
        await project_repo.save(project)
        await member_repo.save(member)
        await pm_repo.add(project.project_id, member.member_id)

        await project_repo.delete(project.project_id)

        ids = await pm_repo.list_member_ids(project.project_id)
        assert ids == []

    async def test_scoped_per_project(self, session_factory) -> None:
        project_repo = SqlAlchemyProjectRepository(session_factory)
        member_repo = SqlAlchemyMemberRepository(session_factory)
        pm_repo = SqlAlchemyProjectMemberRepository(session_factory)

        pj1 = _project("PJ1")
        pj2 = _project("PJ2")
        m1 = _member("田中")
        m2 = _member("佐藤")
        await project_repo.save(pj1)
        await project_repo.save(pj2)
        await member_repo.save(m1)
        await member_repo.save(m2)
        await pm_repo.add(pj1.project_id, m1.member_id)
        await pm_repo.add(pj2.project_id, m2.member_id)

        pj1_members = await member_repo.find_by_project_id(pj1.project_id)
        pj2_members = await member_repo.find_by_project_id(pj2.project_id)

        assert len(pj1_members) == 1
        assert pj1_members[0].member_id == m1.member_id
        assert len(pj2_members) == 1
        assert pj2_members[0].member_id == m2.member_id

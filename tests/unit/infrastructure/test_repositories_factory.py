"""RepositoryBundle ファクトリのユニットテスト。"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.infrastructure.repositories.factory import build_repositories
from src.infrastructure.repositories.in_memory import (
    InMemoryAlertRepository,
    InMemoryDailyReportRepository,
    InMemoryMemberRepository,
    InMemoryProjectRepository,
)
from src.infrastructure.repositories.sqlalchemy import (
    SqlAlchemyAlertRepository,
    SqlAlchemyDailyReportRepository,
    SqlAlchemyMemberRepository,
    SqlAlchemyProjectRepository,
)


class TestBuildRepositories:
    def test_returns_in_memory_when_use_database_false(self) -> None:
        settings = Settings(use_database=False)
        bundle = build_repositories(settings)

        assert isinstance(bundle.project, InMemoryProjectRepository)
        assert isinstance(bundle.member, InMemoryMemberRepository)
        assert isinstance(bundle.alert, InMemoryAlertRepository)
        assert isinstance(bundle.report, InMemoryDailyReportRepository)
        assert bundle.engine is None

    def test_returns_sqlalchemy_when_use_database_true(self) -> None:
        settings = Settings(
            use_database=True,
            database_url="sqlite+aiosqlite:///:memory:",
        )
        bundle = build_repositories(settings)

        assert isinstance(bundle.project, SqlAlchemyProjectRepository)
        assert isinstance(bundle.member, SqlAlchemyMemberRepository)
        assert isinstance(bundle.alert, SqlAlchemyAlertRepository)
        assert isinstance(bundle.report, SqlAlchemyDailyReportRepository)
        assert bundle.engine is not None


@pytest.mark.asyncio
async def test_sqlalchemy_bundle_can_persist_and_dispose() -> None:
    """SqlAlchemy bundle が実際に save できる + engine.dispose() で後始末できる。"""
    from src.domain.member.aggregate import Member
    from src.domain.member.value_objects import MemberId, MemberRole
    from src.infrastructure.db.base import Base

    settings = Settings(
        use_database=True,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    bundle = build_repositories(settings)
    try:
        async with bundle.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        member = Member(
            member_id=MemberId.generate(),
            external_id="ext-001",
            name="テスト太郎",
            role=MemberRole.DEVELOPER,
        )
        await bundle.member.save(member)
        found = await bundle.member.find_by_id(member.member_id)
        assert found is not None
        assert found.name == "テスト太郎"
    finally:
        await bundle.engine.dispose()

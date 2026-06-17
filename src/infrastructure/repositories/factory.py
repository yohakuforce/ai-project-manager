"""リポジトリファクトリ。

settings.use_database に基づいて、SqlAlchemy 実装 / インメモリ実装を切り替える。
シングルトンとしてプロセス内で 1 セット保持する。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config.settings import Settings
from src.domain.alert.repository import AlertRepository
from src.domain.gate.repository import LeaderGateRepository
from src.domain.member.repository import MemberRepository, ProjectMemberRepository
from src.domain.project.repository import ProjectRepository
from src.domain.reporting.repository import DailyReportRepository
from src.infrastructure.repositories.in_memory import (
    InMemoryAlertRepository,
    InMemoryDailyReportRepository,
    InMemoryLeaderGateRepository,
    InMemoryMemberRepository,
    InMemoryProjectMemberRepository,
    InMemoryProjectRepository,
)
from src.infrastructure.repositories.sqlalchemy import (
    SqlAlchemyAlertRepository,
    SqlAlchemyDailyReportRepository,
    SqlAlchemyLeaderGateRepository,
    SqlAlchemyMemberRepository,
    SqlAlchemyProjectMemberRepository,
    SqlAlchemyProjectRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepositoryBundle:
    """集約のリポジトリ束。DI コンテナから取り出して各 Service に注入する。

    gate（リーダー確認ゲート）も use_database に追従する。確認が翌日になっても
    保持されるよう、use_database=True では PostgreSQL に永続化する。
    """

    project: ProjectRepository
    member: MemberRepository
    project_member: ProjectMemberRepository
    alert: AlertRepository
    report: DailyReportRepository
    gate: LeaderGateRepository
    # SQLAlchemy 実装時はライフサイクル管理用に engine も保持
    engine: Any | None = None


def build_repositories(settings: Settings) -> RepositoryBundle:
    """settings から RepositoryBundle を構築する。

    判定:
      - ``use_database = True`` → SqlAlchemy 実装（database_url から engine 生成）
      - それ以外 → InMemory 実装（dev / 単体テスト用）
    """
    if not settings.use_database:
        logger.warning(
            "USE_DATABASE=False のため InMemory リポジトリを使用します。"
            "production では USE_DATABASE=True に設定してください。"
        )
        member_repo = InMemoryMemberRepository()
        return RepositoryBundle(
            project=InMemoryProjectRepository(),
            member=member_repo,
            project_member=InMemoryProjectMemberRepository(member_repo),
            alert=InMemoryAlertRepository(),
            report=InMemoryDailyReportRepository(),
            gate=InMemoryLeaderGateRepository(),
        )

    engine = create_async_engine(
        settings.database_url,
        echo=not settings.is_production,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return RepositoryBundle(
        project=SqlAlchemyProjectRepository(session_factory),
        member=SqlAlchemyMemberRepository(session_factory),
        project_member=SqlAlchemyProjectMemberRepository(session_factory),
        alert=SqlAlchemyAlertRepository(session_factory),
        report=SqlAlchemyDailyReportRepository(session_factory),
        gate=SqlAlchemyLeaderGateRepository(session_factory),
        engine=engine,
    )

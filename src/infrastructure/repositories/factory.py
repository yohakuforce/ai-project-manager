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
from src.domain.member.repository import MemberRepository
from src.domain.project.repository import ProjectRepository
from src.domain.reporting.repository import DailyReportRepository
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

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepositoryBundle:
    """4 集約のリポジトリ束。DI コンテナから取り出して各 Service に注入する。"""

    project: ProjectRepository
    member: MemberRepository
    alert: AlertRepository
    report: DailyReportRepository
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
        return RepositoryBundle(
            project=InMemoryProjectRepository(),
            member=InMemoryMemberRepository(),
            alert=InMemoryAlertRepository(),
            report=InMemoryDailyReportRepository(),
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
        alert=SqlAlchemyAlertRepository(session_factory),
        report=SqlAlchemyDailyReportRepository(session_factory),
        engine=engine,
    )

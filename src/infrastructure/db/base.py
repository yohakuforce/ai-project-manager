"""
SQLAlchemy ORM ベース設定。
非同期エンジン・セッションファクトリ・DeclarativeBase を定義する。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import get_settings


class Base(DeclarativeBase):
    """すべての ORM モデルが継承するベースクラス。"""

    pass


def create_engine():
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=not settings.is_production,
        pool_pre_ping=True,
    )


def create_session_factory(engine=None):
    if engine is None:
        engine = create_engine()
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

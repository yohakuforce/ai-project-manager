"""
FastAPI アプリケーションエントリポイント。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import (
    alert,
    assign,
    guide,
    overview,
    pipeline,
    plan,
    registry,
    registry_ui,
    settings_ui,
    track,
)
from src.config import get_settings

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.infrastructure.scheduler.pipeline_scheduler import PipelineScheduler

logger = logging.getLogger(__name__)


def _build_scheduler(settings: Settings) -> PipelineScheduler:
    """settings から日次パイプラインスケジューラを組み立てる。

    遅延 import で apscheduler 依存をアプリ起動パスの外に保つ（テストでの import を軽く）。
    """
    from src.api import deps
    from src.application.scheduler.daily_pipeline import DailyPipelineOrchestrator
    from src.application.scheduler.schedule_plan import ScheduleConfig
    from src.infrastructure.scheduler.pipeline_scheduler import PipelineScheduler

    orchestrator = DailyPipelineOrchestrator(
        project_repository=deps.get_project_repo(),
        track_service=deps.get_track_service(),
        overview_service=deps.get_overview_service(),
        alert_service=deps.get_alert_service(),
        standup_service=deps.get_standup_service(),
        wrap_up_service=deps.get_wrap_up_service(),
    )
    config = ScheduleConfig(
        standup_hour=settings.standup_hour,
        standup_minute=settings.standup_minute,
        report_hour=settings.report_hour,
        report_minute=settings.report_minute,
        reminder_hour=settings.reminder_hour,
        reminder_minute=settings.reminder_minute,
        wrap_up_hour=settings.wrap_up_hour,
        wrap_up_minute=settings.wrap_up_minute,
        scan_interval_minutes=settings.alert_scan_interval_minutes,
    )
    return PipelineScheduler(
        orchestrator=orchestrator,
        config=config,
        timezone=settings.scheduler_timezone,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """起動時に日次スケジューラを開始し、終了時に停止する。

    scheduler_enabled が False、または起動に失敗しても API 本体は継続する
    （スケジューラの不調が API を巻き込まない）。
    """
    settings = get_settings()
    scheduler = None
    if settings.scheduler_enabled:
        try:
            scheduler = _build_scheduler(settings)
            scheduler.start()
            app.state.scheduler = scheduler
        except Exception as exc:  # 起動失敗で API を止めない
            logger.error("日次スケジューラの起動に失敗しました（API は継続）: %s", exc)
            scheduler = None
    else:
        logger.info("scheduler_enabled=False のためスケジューラは起動しません。")
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown()


def create_app() -> FastAPI:
    """FastAPI アプリを生成して返す。"""
    settings = get_settings()

    app = FastAPI(
        title="AI-Project-Manager API",
        version="0.1.0",
        description="AI によるプロジェクト管理支援 API",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=_lifespan,
    )

    # CORS 設定
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ヘルスチェック（認証不要）
    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    # 設定 GUI / 運用ガイド / 登録 GUI（認証不要 — ローカル管理者向け、localhost バインド必須）
    app.include_router(settings_ui.router)
    app.include_router(guide.router)
    app.include_router(registry_ui.router)

    # ルーター登録
    app.include_router(registry.router, prefix="/api/v1")
    app.include_router(plan.router, prefix="/api/v1")
    app.include_router(assign.router, prefix="/api/v1")
    app.include_router(track.router, prefix="/api/v1")
    app.include_router(alert.router, prefix="/api/v1")
    app.include_router(overview.router, prefix="/api/v1")
    app.include_router(pipeline.router, prefix="/api/v1")

    return app


app = create_app()

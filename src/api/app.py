"""
FastAPI アプリケーションエントリポイント。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import alert, assign, overview, plan, settings_ui, track
from src.config import get_settings


def create_app() -> FastAPI:
    """FastAPI アプリを生成して返す。"""
    settings = get_settings()

    app = FastAPI(
        title="AI-Project-Manager API",
        version="0.1.0",
        description="AI によるプロジェクト管理支援 API",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
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

    # 設定 GUI（認証不要 — ローカル管理者向け、localhost バインド必須）
    app.include_router(settings_ui.router)

    # ルーター登録
    app.include_router(plan.router, prefix="/api/v1")
    app.include_router(assign.router, prefix="/api/v1")
    app.include_router(track.router, prefix="/api/v1")
    app.include_router(alert.router, prefix="/api/v1")
    app.include_router(overview.router, prefix="/api/v1")

    return app


app = create_app()

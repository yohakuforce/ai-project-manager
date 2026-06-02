"""
スケジューラの lifespan 起動を検証する統合テスト。

`with TestClient(app)` は FastAPI の lifespan を発火させるため、ここでだけ
スケジューラが実際に起動する（通常の TestClient(app) 直接利用では起動しない）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config.settings import get_settings


def test_scheduler_starts_when_enabled() -> None:
    get_settings.cache_clear()
    app = create_app()
    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            # lifespan でスケジューラが起動し、計画が app.state に載る
            assert hasattr(app.state, "scheduler")
            assert len(app.state.scheduler.plan) >= 2
    finally:
        get_settings.cache_clear()


def test_scheduler_disabled_does_not_start(monkeypatch) -> None:
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            # 無効時は app.state.scheduler を設定しない
            assert not hasattr(app.state, "scheduler")
    finally:
        get_settings.cache_clear()

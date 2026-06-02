"""
設定 UI ルーターの統合テスト。

テスト対象:
  GET  /settings            フォーム HTML 描画・フィールド名を含む
  POST /settings            .env 書き込み・シークレットマスク保持・バリデーション
  POST /settings/test/context-hub  Context-Hub 接続テスト（httpx モック）
  POST /settings/test/delivery     通知チャンネル接続テスト（notifier モック）
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.routes.settings_ui import (
    _is_masked,
    _load_env_lines,
    _mask_secret,
    _update_env_lines,
)
from src.config.settings import Settings, get_settings

# ---- フィクスチャ --------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """tmp_path 配下の .env を使う TestClient。設定キャッシュはクリア済み。"""
    env_file = tmp_path / ".env"
    env_file.touch()

    # SETTINGS_UI_ENV_PATH を上書きして tmp .env を指す
    with patch.dict(os.environ, {"SETTINGS_UI_ENV_PATH": str(env_file)}):
        get_settings.cache_clear()
        app = create_app()
        yield TestClient(app)

    get_settings.cache_clear()


@pytest.fixture
def default_settings() -> Settings:
    return Settings()


# ---- ユニットヘルパーテスト ----------------------------------------------


class TestMaskSecret:
    def test_empty_returns_empty(self) -> None:
        assert _mask_secret("") == ""

    def test_short_value_returns_dots(self) -> None:
        assert _mask_secret("abc") == "••••"

    def test_long_value_shows_last4(self) -> None:
        result = _mask_secret("xoxb-1234567890abcdef")
        assert result.startswith("••••")
        assert result.endswith("cdef")
        assert "xoxb" not in result

    def test_exactly4_chars(self) -> None:
        # 4文字以下はすべて ••••
        assert _mask_secret("1234") == "••••"


class TestIsMasked:
    def test_masked_prefix(self) -> None:
        assert _is_masked("••••abcd") is True

    def test_empty_string(self) -> None:
        assert _is_masked("") is True

    def test_plain_value(self) -> None:
        assert _is_masked("xoxb-plaintoken") is False


class TestUpdateEnvLines:
    def test_adds_new_key_at_end(self) -> None:
        lines = ["EXISTING=yes\n"]
        result = _update_env_lines(lines, {"NEW_KEY": "new_val"})
        assert "NEW_KEY=new_val\n" in result
        assert "EXISTING=yes\n" in result

    def test_replaces_existing_key(self) -> None:
        lines = ["FOO=old\n"]
        result = _update_env_lines(lines, {"FOO": "new"})
        assert result == ["FOO=new\n"]

    def test_preserves_comments(self) -> None:
        lines = ["# comment\n", "FOO=val\n"]
        result = _update_env_lines(lines, {"FOO": "updated"})
        assert result[0] == "# comment\n"

    def test_preserves_unknown_keys(self) -> None:
        lines = ["SECRET=keep\n"]
        result = _update_env_lines(lines, {"OTHER": "x"})
        assert any("SECRET=keep" in line for line in result)


class TestLoadEnvLines:
    def test_nonexistent_returns_empty(self, tmp_path: Path) -> None:
        result = _load_env_lines(tmp_path / "missing.env")
        assert result == []

    def test_reads_existing_file(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        p.write_text("KEY=val\n", encoding="utf-8")
        assert _load_env_lines(p) == ["KEY=val\n"]


# ---- GET /settings -------------------------------------------------------


class TestGetSettingsPage:
    def test_returns_200(self, client: TestClient) -> None:
        response = client.get("/settings")
        assert response.status_code == 200

    def test_content_type_is_html(self, client: TestClient) -> None:
        response = client.get("/settings")
        assert "text/html" in response.headers["content-type"]

    def test_contains_field_names(self, client: TestClient) -> None:
        response = client.get("/settings")
        body = response.text
        expected_fields = [
            "context_hub_base_url",
            "context_hub_api_key",
            "context_hub_use_mock",
            "llm_provider",
            "claude_code_cli_path",
            "notification_channel",
            "slack_bot_token",
            "slack_notification_channel",
            "google_service_account_json",
            "google_sheet_id",
            "notification_local_dir",
            "standup_hour",
            "report_hour",
            "reminder_hour",
            "wrap_up_hour",
            "wrap_up_minute",
            "alert_scan_interval_minutes",
        ]
        for field in expected_fields:
            assert field in body, f"フィールド '{field}' が HTML に含まれていません"

    def test_secret_fields_are_masked(self, client: TestClient, tmp_path: Path) -> None:
        """シークレット値はフル値ではなく ••••... でマスクされること。"""
        env_file = tmp_path / ".env"
        env_file.write_text("SLACK_BOT_TOKEN=xoxb-secret-full-token\n", encoding="utf-8")
        with patch.dict(
            os.environ,
            {"SETTINGS_UI_ENV_PATH": str(env_file), "SLACK_BOT_TOKEN": "xoxb-secret-full-token"},
        ):
            get_settings.cache_clear()
            response = client.get("/settings")
        get_settings.cache_clear()
        assert "xoxb-secret-full-token" not in response.text

    def test_no_api_key_required(self, client: TestClient) -> None:
        """認証ヘッダーなしで 200 が返ること（X-Api-Key exempt）。"""
        response = client.get("/settings")
        assert response.status_code == 200


# ---- POST /settings -------------------------------------------------------


class TestPostSettings:
    def _base_form(self) -> dict[str, str]:
        return {
            "context_hub_base_url": "http://hub.example.com/api/v1",
            "context_hub_api_key": "",
            "context_hub_use_mock": "false",
            "llm_provider": "claude-code",
            "claude_code_cli_path": "/usr/local/bin/claude",
            "notification_channel": "local_file",
            "slack_bot_token": "",
            "slack_notification_channel": "#alerts",
            "google_service_account_json": "",
            "google_sheet_id": "",
            "notification_local_dir": "/tmp/notifications",
            "standup_hour": "9",
            "standup_minute": "0",
            "report_hour": "14",
            "report_minute": "0",
            "reminder_hour": "17",
            "reminder_minute": "0",
            "wrap_up_hour": "17",
            "wrap_up_minute": "30",
            "alert_scan_interval_minutes": "15",
        }

    def test_returns_200_with_success_banner(self, client: TestClient, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"SETTINGS_UI_ENV_PATH": str(tmp_path / ".env")}):
            get_settings.cache_clear()
            response = client.post("/settings", data=self._base_form())
        assert response.status_code == 200
        assert "保存しました" in response.text

    def test_writes_env_file(self, client: TestClient, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        with patch.dict(os.environ, {"SETTINGS_UI_ENV_PATH": str(env_file)}):
            get_settings.cache_clear()
            client.post("/settings", data=self._base_form())

        content = env_file.read_text(encoding="utf-8")
        assert "CONTEXT_HUB_BASE_URL=http://hub.example.com/api/v1" in content
        assert "LLM_PROVIDER=claude-code" in content
        assert "NOTIFICATION_CHANNEL=local_file" in content

    def test_round_trips_non_secret_values(self, client: TestClient, tmp_path: Path) -> None:
        """POST後に .env が書き込まれ、GET が最新値を返すこと。"""
        env_file = tmp_path / ".env"
        env_file.touch()

        with patch.dict(os.environ, {"SETTINGS_UI_ENV_PATH": str(env_file)}):
            get_settings.cache_clear()
            # POST: フォーム送信
            client.post("/settings", data=self._base_form())

        # .env に書き込まれた内容を直接確認（round-trip の正規テスト）
        content = env_file.read_text(encoding="utf-8")
        assert "CONTEXT_HUB_BASE_URL=http://hub.example.com/api/v1" in content

        # GET: 書き込んだ値が画面に出ること（Settings を再構築して確認）
        with patch.dict(
            os.environ,
            {
                "SETTINGS_UI_ENV_PATH": str(env_file),
                "CONTEXT_HUB_BASE_URL": "http://hub.example.com/api/v1",
            },
        ):
            get_settings.cache_clear()
            response = client.get("/settings")
        get_settings.cache_clear()

        assert "http://hub.example.com/api/v1" in response.text

    def test_does_not_write_empty_secret(self, client: TestClient, tmp_path: Path) -> None:
        """空のシークレットフィールドは既存値（空）が保たれること。"""
        env_file = tmp_path / ".env"
        env_file.write_text("SLACK_BOT_TOKEN=existing-token\n", encoding="utf-8")
        form = {**self._base_form(), "slack_bot_token": ""}
        with patch.dict(os.environ, {"SETTINGS_UI_ENV_PATH": str(env_file)}):
            get_settings.cache_clear()
            with patch("src.api.routes.settings_ui.get_settings") as mock_gs:
                mock_settings = Settings()
                object.__setattr__(mock_settings, "slack_bot_token", "existing-token")
                mock_gs.return_value = mock_settings
                mock_gs.cache_clear = lambda: None
                client.post("/settings", data=form)

        content = env_file.read_text(encoding="utf-8")
        assert "SLACK_BOT_TOKEN=existing-token" in content

    def test_does_not_write_masked_secret(self, client: TestClient, tmp_path: Path) -> None:
        """マスク済み値が送信された場合、既存値が維持されること。"""
        env_file = tmp_path / ".env"
        form = {**self._base_form(), "slack_bot_token": "••••1234"}
        with patch.dict(os.environ, {"SETTINGS_UI_ENV_PATH": str(env_file)}):
            get_settings.cache_clear()
            with patch("src.api.routes.settings_ui.get_settings") as mock_gs:
                mock_settings = Settings()
                object.__setattr__(mock_settings, "slack_bot_token", "real-token-abcd1234")
                mock_gs.return_value = mock_settings
                mock_gs.cache_clear = lambda: None
                client.post("/settings", data=form)

        content = env_file.read_text(encoding="utf-8")
        assert "SLACK_BOT_TOKEN=real-token-abcd1234" in content

    def test_invalid_channel_returns_422(self, client: TestClient, tmp_path: Path) -> None:
        form = {**self._base_form(), "notification_channel": "carrier_pigeon"}
        with patch.dict(os.environ, {"SETTINGS_UI_ENV_PATH": str(tmp_path / ".env")}):
            get_settings.cache_clear()
            response = client.post("/settings", data=form)
        assert response.status_code == 422
        assert "carrier_pigeon" in response.text

    def test_invalid_hour_returns_422(self, client: TestClient, tmp_path: Path) -> None:
        form = {**self._base_form(), "report_hour": "25"}
        with patch.dict(os.environ, {"SETTINGS_UI_ENV_PATH": str(tmp_path / ".env")}):
            get_settings.cache_clear()
            response = client.post("/settings", data=form)
        assert response.status_code == 422

    def test_invalid_interval_returns_422(self, client: TestClient, tmp_path: Path) -> None:
        form = {**self._base_form(), "alert_scan_interval_minutes": "0"}
        with patch.dict(os.environ, {"SETTINGS_UI_ENV_PATH": str(tmp_path / ".env")}):
            get_settings.cache_clear()
            response = client.post("/settings", data=form)
        assert response.status_code == 422

    def test_preserves_unknown_env_lines(self, client: TestClient, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("CUSTOM_VAR=keep_this\n", encoding="utf-8")
        with patch.dict(os.environ, {"SETTINGS_UI_ENV_PATH": str(env_file)}):
            get_settings.cache_clear()
            client.post("/settings", data=self._base_form())

        content = env_file.read_text(encoding="utf-8")
        assert "CUSTOM_VAR=keep_this" in content


# ---- POST /settings/test/context-hub ------------------------------------


class TestContextHubTest:
    def _form(self) -> dict[str, str]:
        return {
            "context_hub_base_url": "http://hub.example.com/api/v1",
            "context_hub_api_key": "",
            "context_hub_use_mock": "false",
        }

    def test_returns_ok_true_on_200(self, client: TestClient) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("src.api.routes.settings_ui.httpx.AsyncClient", return_value=mock_client):
            response = client.post("/settings/test/context-hub", data=self._form())

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert "200" in body["detail"]

    def test_returns_ok_false_on_http_error(self, client: TestClient) -> None:
        import httpx as _httpx

        mock_request = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=_httpx.HTTPStatusError("401", request=mock_request, response=mock_resp)
        )

        with patch("src.api.routes.settings_ui.httpx.AsyncClient", return_value=mock_client):
            response = client.post("/settings/test/context-hub", data=self._form())

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "401" in body["detail"]

    def test_returns_ok_false_on_connection_error(self, client: TestClient) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))

        with patch("src.api.routes.settings_ui.httpx.AsyncClient", return_value=mock_client):
            response = client.post("/settings/test/context-hub", data=self._form())

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "connection refused" in body["detail"]

    def test_response_has_ok_and_detail_keys(self, client: TestClient) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("src.api.routes.settings_ui.httpx.AsyncClient", return_value=mock_client):
            response = client.post("/settings/test/context-hub", data=self._form())

        body = response.json()
        assert "ok" in body
        assert "detail" in body


# ---- POST /settings/test/delivery ----------------------------------------


class TestDeliveryTest:
    def _form(self, channel: str = "in_memory") -> dict[str, str]:
        return {
            "notification_channel": channel,
            "slack_bot_token": "",
            "slack_notification_channel": "#alerts",
            "google_service_account_json": "",
            "google_sheet_id": "",
            "notification_local_dir": "/tmp/test-notif",
        }

    def test_in_memory_returns_ok_true(self, client: TestClient) -> None:
        response = client.post("/settings/test/delivery", data=self._form("in_memory"))
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True

    def test_response_has_ok_and_detail_keys(self, client: TestClient) -> None:
        response = client.post("/settings/test/delivery", data=self._form("in_memory"))
        body = response.json()
        assert "ok" in body
        assert "detail" in body

    def test_invalid_channel_returns_ok_false(self, client: TestClient) -> None:
        response = client.post(
            "/settings/test/delivery",
            data=self._form("invalid_channel"),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False

    def test_healthcheck_failure_returns_ok_false(self, client: TestClient) -> None:
        mock_notifier = AsyncMock()
        mock_notifier.healthcheck = AsyncMock(return_value=False)

        with patch("src.api.routes.settings_ui.build_notifier", return_value=mock_notifier):
            response = client.post("/settings/test/delivery", data=self._form("local_file"))

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False

    def test_healthcheck_exception_returns_ok_false(self, client: TestClient) -> None:
        mock_notifier = AsyncMock()
        mock_notifier.healthcheck = AsyncMock(side_effect=Exception("service down"))

        with patch("src.api.routes.settings_ui.build_notifier", return_value=mock_notifier):
            response = client.post("/settings/test/delivery", data=self._form("slack"))

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "service down" in body["detail"]

    def test_local_file_channel_succeeds(self, client: TestClient, tmp_path: Path) -> None:
        """local_file チャンネルは外部依存なく healthcheck が通ること。"""
        form = {**self._form("local_file"), "notification_local_dir": str(tmp_path)}
        response = client.post("/settings/test/delivery", data=form)
        assert response.status_code == 200
        body = response.json()
        # local_file healthcheck は常に True を返す
        assert body["ok"] is True

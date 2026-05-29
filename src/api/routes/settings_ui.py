"""
設定管理 GUI ルーター。

ローカル運用者向けの簡易 Web UI。認証不要（X-Api-Key exempt）。
IMPORTANT: このエンドポイントは localhost 専用のツールです。
           外部公開サーバーでは必ず uvicorn --host 127.0.0.1 で起動してください。

Routes:
  GET  /settings          設定フォームを HTML で返す
  POST /settings          フォーム送信 → .env ファイルに書き込む
  POST /settings/test/context-hub   Context-Hub 接続テスト → JSON
  POST /settings/test/delivery      通知チャンネル接続テスト → JSON
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from src.config.settings import Settings, get_settings
from src.infrastructure.notifiers.factory import build_notifier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings-ui"])

# ---- 定数 ----------------------------------------------------------------

VALID_CHANNELS = frozenset({"slack", "google_sheets", "local_file", "in_memory"})

# シークレットフィールド名（マスキング対象）
_SECRET_FIELDS = frozenset(
    {
        "context_hub_api_key",
        "slack_bot_token",
        "google_service_account_json",
    }
)

# .env に書き込む既知キーの一覧（大文字 env 名 → Settings フィールド名）
_KNOWN_KEYS: dict[str, str] = {
    "CONTEXT_HUB_BASE_URL": "context_hub_base_url",
    "CONTEXT_HUB_API_KEY": "context_hub_api_key",
    "CONTEXT_HUB_USE_MOCK": "context_hub_use_mock",
    "LLM_PROVIDER": "llm_provider",
    "CLAUDE_CODE_CLI_PATH": "claude_code_cli_path",
    "NOTIFICATION_CHANNEL": "notification_channel",
    "SLACK_BOT_TOKEN": "slack_bot_token",
    "SLACK_NOTIFICATION_CHANNEL": "slack_notification_channel",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "google_service_account_json",
    "GOOGLE_SHEET_ID": "google_sheet_id",
    "NOTIFICATION_LOCAL_DIR": "notification_local_dir",
    "DAILY_REPORT_DELIVERY_HOUR": "daily_report_delivery_hour",
    "DAILY_OVERVIEW_GENERATION_HOUR": "daily_overview_generation_hour",
    "ALERT_SCAN_INTERVAL_MINUTES": "alert_scan_interval_minutes",
}

# ---- ヘルパー: .env 操作 -------------------------------------------------


def _load_env_lines(env_path: Path) -> list[str]:
    """既存 .env ファイルの行リストを返す。存在しない場合は空リスト。"""
    if not env_path.exists():
        return []
    return env_path.read_text(encoding="utf-8").splitlines(keepends=True)


def _update_env_lines(lines: list[str], updates: dict[str, str]) -> list[str]:
    """
    既存行リストに updates（大文字キー→値）を反映した新しいリストを返す。

    - 既存キーは同一行を置換
    - 未知行（コメント・空行・他キー）は保持
    - 新規キーは末尾に追加
    """
    result: list[str] = []
    written: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            result.append(line)
            continue
        match = re.match(r"^([A-Z0-9_]+)\s*=", stripped)
        if match:
            key = match.group(1)
            if key in updates:
                result.append(f"{key}={updates[key]}\n")
                written.add(key)
                continue
        result.append(line)

    # 末尾に未書き込みキーを追加
    for key, value in updates.items():
        if key not in written:
            result.append(f"{key}={value}\n")

    return result


def _mask_secret(value: str) -> str:
    """シークレット値を ••••last4 形式でマスクする。空の場合は空文字を返す。"""
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"


def _is_masked(value: str) -> bool:
    """フォーム送信値がマスク済み（変更なし）かどうか判定する。"""
    return value.startswith("••••") or value == ""


def _resolve_env_path(override: str | None = None) -> Path:
    """プロジェクトルートの .env パスを返す。テスト時は override を使用。"""
    if override:
        return Path(override)
    return Path(os.environ.get("SETTINGS_UI_ENV_PATH", ".env"))


# ---- ヘルパー: バリデーション -------------------------------------------


def _validate_channel(channel: str) -> str | None:
    """有効なチャンネル名か検証。不正なら理由文字列を返す。"""
    if channel not in VALID_CHANNELS:
        return f"無効な notification_channel: '{channel}'. 選択肢: {', '.join(sorted(VALID_CHANNELS))}"
    return None


def _validate_hour(value: str, label: str) -> str | None:
    """0〜23 の整数か検証。不正なら理由文字列を返す。"""
    try:
        hour = int(value)
    except ValueError:
        return f"{label} は整数で指定してください。"
    if not (0 <= hour <= 23):
        return f"{label} は 0〜23 の範囲で指定してください。"
    return None


def _validate_interval(value: str) -> str | None:
    """1〜1440 の整数か検証。不正なら理由文字列を返す。"""
    try:
        minutes = int(value)
    except ValueError:
        return "alert_scan_interval_minutes は整数で指定してください。"
    if not (1 <= minutes <= 1440):
        return "alert_scan_interval_minutes は 1〜1440 の範囲で指定してください。"
    return None


# ---- HTML テンプレート ---------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI-Project-Manager 設定</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; border-bottom: 2px solid #4f46e5; padding-bottom: 8px; }}
  h2 {{ font-size: 1.05rem; color: #4f46e5; margin-top: 28px; }}
  label {{ display: block; font-size: 0.85rem; font-weight: 600; margin: 14px 0 4px; }}
  input[type=text], input[type=number], select, textarea {{
    width: 100%; padding: 7px 10px; border: 1px solid #d1d5db; border-radius: 6px;
    font-size: 0.9rem; box-sizing: border-box; background: #f9fafb;
  }}
  input[type=text]:focus, select:focus {{ outline: 2px solid #4f46e5; background: #fff; }}
  .hint {{ font-size: 0.75rem; color: #6b7280; margin-top: 2px; }}
  .actions {{ margin-top: 24px; display: flex; gap: 10px; flex-wrap: wrap; }}
  button[type=submit] {{ background: #4f46e5; color: #fff; border: none; padding: 9px 22px;
    border-radius: 6px; font-size: 0.9rem; cursor: pointer; }}
  button[type=submit]:hover {{ background: #4338ca; }}
  .test-btn {{ background: #0ea5e9; color: #fff; border: none; padding: 7px 16px;
    border-radius: 6px; font-size: 0.85rem; cursor: pointer; }}
  .test-btn:hover {{ background: #0284c7; }}
  .banner-ok {{ background: #d1fae5; color: #065f46; padding: 10px 14px; border-radius: 6px;
    margin-bottom: 16px; font-size: 0.9rem; }}
  .banner-err {{ background: #fee2e2; color: #991b1b; padding: 10px 14px; border-radius: 6px;
    margin-bottom: 16px; font-size: 0.9rem; }}
  #test-hub-result, #test-delivery-result {{ margin-top: 6px; font-size: 0.82rem; min-height: 18px; }}
  .ok {{ color: #065f46; }} .err {{ color: #991b1b; }}
  fieldset {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 18px; margin-bottom: 8px; }}
  legend {{ font-weight: 700; font-size: 0.9rem; padding: 0 6px; }}
</style>
</head>
<body>
<h1>AI-Project-Manager 設定</h1>
<p class="hint">ローカル管理者専用ページ。設定は .env ファイルに保存されます。</p>
{banner}
<form method="POST" action="/settings">

  <fieldset>
    <legend>Context-Hub</legend>
    <label for="context_hub_base_url">context_hub_base_url</label>
    <input type="text" id="context_hub_base_url" name="context_hub_base_url"
      value="{context_hub_base_url}">

    <label for="context_hub_api_key">context_hub_api_key <span class="hint">(シークレット)</span></label>
    <input type="text" id="context_hub_api_key" name="context_hub_api_key"
      value="{context_hub_api_key_masked}" placeholder="変更する場合のみ入力">

    <label for="context_hub_use_mock">context_hub_use_mock</label>
    <select id="context_hub_use_mock" name="context_hub_use_mock">
      <option value="true" {mock_true_sel}>true (モック使用)</option>
      <option value="false" {mock_false_sel}>false (本番接続)</option>
    </select>
  </fieldset>

  <fieldset>
    <legend>LLM</legend>
    <label for="llm_provider">llm_provider</label>
    <input type="text" id="llm_provider" name="llm_provider" value="{llm_provider}">
    <p class="hint">選択肢: claude-code | codex | ollama | mock</p>

    <label for="claude_code_cli_path">claude_code_cli_path</label>
    <input type="text" id="claude_code_cli_path" name="claude_code_cli_path"
      value="{claude_code_cli_path}" placeholder="空の場合 PATH から自動検出">
  </fieldset>

  <fieldset>
    <legend>通知チャンネル</legend>
    <label for="notification_channel">notification_channel</label>
    <select id="notification_channel" name="notification_channel">
      <option value="slack" {ch_slack}>Slack</option>
      <option value="google_sheets" {ch_gs}>Google Sheets</option>
      <option value="local_file" {ch_lf}>ローカルファイル</option>
      <option value="in_memory" {ch_mem}>インメモリ (テスト用)</option>
    </select>

    <label for="slack_bot_token">slack_bot_token <span class="hint">(シークレット)</span></label>
    <input type="text" id="slack_bot_token" name="slack_bot_token"
      value="{slack_bot_token_masked}" placeholder="変更する場合のみ入力">

    <label for="slack_notification_channel">slack_notification_channel</label>
    <input type="text" id="slack_notification_channel" name="slack_notification_channel"
      value="{slack_notification_channel}">

    <label for="google_service_account_json">google_service_account_json
      <span class="hint">(シークレット — JSON パスまたは JSON 文字列)</span></label>
    <input type="text" id="google_service_account_json" name="google_service_account_json"
      value="{google_service_account_json_masked}" placeholder="変更する場合のみ入力">

    <label for="google_sheet_id">google_sheet_id</label>
    <input type="text" id="google_sheet_id" name="google_sheet_id"
      value="{google_sheet_id}">

    <label for="notification_local_dir">notification_local_dir</label>
    <input type="text" id="notification_local_dir" name="notification_local_dir"
      value="{notification_local_dir}">
  </fieldset>

  <fieldset>
    <legend>スケジュール</legend>
    <label for="daily_report_delivery_hour">daily_report_delivery_hour (0〜23)</label>
    <input type="number" id="daily_report_delivery_hour" name="daily_report_delivery_hour"
      min="0" max="23" value="{daily_report_delivery_hour}">

    <label for="daily_overview_generation_hour">daily_overview_generation_hour (0〜23)</label>
    <input type="number" id="daily_overview_generation_hour" name="daily_overview_generation_hour"
      min="0" max="23" value="{daily_overview_generation_hour}">

    <label for="alert_scan_interval_minutes">alert_scan_interval_minutes (分)</label>
    <input type="number" id="alert_scan_interval_minutes" name="alert_scan_interval_minutes"
      min="1" max="1440" value="{alert_scan_interval_minutes}">
  </fieldset>

  <div class="actions">
    <button type="submit">保存</button>
    <button type="button" class="test-btn" onclick="testHub()">Context-Hub 接続テスト</button>
    <button type="button" class="test-btn" onclick="testDelivery()">通知チャンネル テスト</button>
  </div>
  <div id="test-hub-result"></div>
  <div id="test-delivery-result"></div>
</form>

<script>
async function testHub() {{
  const el = document.getElementById('test-hub-result');
  el.textContent = '確認中…';
  const form = document.querySelector('form');
  const data = new FormData(form);
  try {{
    const res = await fetch('/settings/test/context-hub', {{ method: 'POST', body: data }});
    const json = await res.json();
    el.textContent = json.ok ? '✓ ' + json.detail : '✗ ' + json.detail;
    el.className = json.ok ? 'ok' : 'err';
  }} catch(e) {{ el.textContent = '✗ ' + e; el.className = 'err'; }}
}}
async function testDelivery() {{
  const el = document.getElementById('test-delivery-result');
  el.textContent = '確認中…';
  const form = document.querySelector('form');
  const data = new FormData(form);
  try {{
    const res = await fetch('/settings/test/delivery', {{ method: 'POST', body: data }});
    const json = await res.json();
    el.textContent = json.ok ? '✓ ' + json.detail : '✗ ' + json.detail;
    el.className = json.ok ? 'ok' : 'err';
  }} catch(e) {{ el.textContent = '✗ ' + e; el.className = 'err'; }}
}}
</script>
</body>
</html>"""


def _render_page(settings: Settings, banner: str = "") -> str:
    """設定値を HTML テンプレートに埋め込んで返す。"""
    ch = settings.notification_channel
    return _HTML_TEMPLATE.format(
        banner=banner,
        context_hub_base_url=settings.context_hub_base_url,
        context_hub_api_key_masked=_mask_secret(settings.context_hub_api_key),
        mock_true_sel="selected" if settings.context_hub_use_mock else "",
        mock_false_sel="" if settings.context_hub_use_mock else "selected",
        llm_provider=settings.llm_provider,
        claude_code_cli_path=settings.claude_code_cli_path,
        ch_slack="selected" if ch == "slack" else "",
        ch_gs="selected" if ch == "google_sheets" else "",
        ch_lf="selected" if ch == "local_file" else "",
        ch_mem="selected" if ch == "in_memory" else "",
        slack_bot_token_masked=_mask_secret(settings.slack_bot_token),
        slack_notification_channel=settings.slack_notification_channel,
        google_service_account_json_masked=_mask_secret(settings.google_service_account_json),
        google_sheet_id=settings.google_sheet_id,
        notification_local_dir=settings.notification_local_dir,
        daily_report_delivery_hour=settings.daily_report_delivery_hour,
        daily_overview_generation_hour=settings.daily_overview_generation_hour,
        alert_scan_interval_minutes=settings.alert_scan_interval_minutes,
    )


# ---- フォーム送信処理 ---------------------------------------------------


def _build_updates_from_form(form_data: dict[str, str], current: Settings) -> dict[str, str]:
    """
    フォームデータと現在の Settings から .env 書き込み用の更新辞書を構築する。

    シークレットフィールドが空/マスク済みの場合は現在値を保持する。
    """
    updates: dict[str, str] = {}

    # テキスト/数値フィールド
    plain_map = {
        "CONTEXT_HUB_BASE_URL": "context_hub_base_url",
        "CONTEXT_HUB_USE_MOCK": "context_hub_use_mock",
        "LLM_PROVIDER": "llm_provider",
        "CLAUDE_CODE_CLI_PATH": "claude_code_cli_path",
        "NOTIFICATION_CHANNEL": "notification_channel",
        "SLACK_NOTIFICATION_CHANNEL": "slack_notification_channel",
        "GOOGLE_SHEET_ID": "google_sheet_id",
        "NOTIFICATION_LOCAL_DIR": "notification_local_dir",
        "DAILY_REPORT_DELIVERY_HOUR": "daily_report_delivery_hour",
        "DAILY_OVERVIEW_GENERATION_HOUR": "daily_overview_generation_hour",
        "ALERT_SCAN_INTERVAL_MINUTES": "alert_scan_interval_minutes",
    }
    for env_key, field_name in plain_map.items():
        form_value = form_data.get(field_name, "")
        updates[env_key] = form_value

    # シークレットフィールド: 空/マスク済みなら現在値を維持
    secret_map = {
        "CONTEXT_HUB_API_KEY": ("context_hub_api_key", current.context_hub_api_key),
        "SLACK_BOT_TOKEN": ("slack_bot_token", current.slack_bot_token),
        "GOOGLE_SERVICE_ACCOUNT_JSON": (
            "google_service_account_json",
            current.google_service_account_json,
        ),
    }
    for env_key, (field_name, current_val) in secret_map.items():
        submitted = form_data.get(field_name, "")
        if _is_masked(submitted):
            updates[env_key] = current_val
        else:
            updates[env_key] = submitted

    return updates


def _settings_from_form(form_data: dict[str, str], current: Settings) -> Settings:
    """フォームデータから一時的な Settings オブジェクトを生成する（接続テスト用）。"""
    hub_key_raw = form_data.get("context_hub_api_key", "")
    hub_key = current.context_hub_api_key if _is_masked(hub_key_raw) else hub_key_raw

    slack_token_raw = form_data.get("slack_bot_token", "")
    slack_token = current.slack_bot_token if _is_masked(slack_token_raw) else slack_token_raw

    gsa_raw = form_data.get("google_service_account_json", "")
    gsa = current.google_service_account_json if _is_masked(gsa_raw) else gsa_raw

    return Settings(
        context_hub_base_url=form_data.get(
            "context_hub_base_url", current.context_hub_base_url
        ),
        context_hub_api_key=hub_key,
        context_hub_use_mock=(
            form_data.get("context_hub_use_mock", "true").lower() == "true"
        ),
        llm_provider=form_data.get("llm_provider", current.llm_provider),
        claude_code_cli_path=form_data.get(
            "claude_code_cli_path", current.claude_code_cli_path
        ),
        notification_channel=form_data.get(
            "notification_channel", current.notification_channel
        ),
        slack_bot_token=slack_token,
        slack_notification_channel=form_data.get(
            "slack_notification_channel", current.slack_notification_channel
        ),
        google_service_account_json=gsa,
        google_sheet_id=form_data.get("google_sheet_id", current.google_sheet_id),
        notification_local_dir=form_data.get(
            "notification_local_dir", current.notification_local_dir
        ),
    )


# ---- エンドポイント ------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def get_settings_page() -> Response:
    """設定フォームを HTML で返す。"""
    settings = get_settings()
    return HTMLResponse(content=_render_page(settings))


@router.post("", response_class=HTMLResponse)
async def post_settings(
    context_hub_base_url: Annotated[str, Form()] = "",
    context_hub_api_key: Annotated[str, Form()] = "",
    context_hub_use_mock: Annotated[str, Form()] = "true",
    llm_provider: Annotated[str, Form()] = "",
    claude_code_cli_path: Annotated[str, Form()] = "",
    notification_channel: Annotated[str, Form()] = "",
    slack_bot_token: Annotated[str, Form()] = "",
    slack_notification_channel: Annotated[str, Form()] = "",
    google_service_account_json: Annotated[str, Form()] = "",
    google_sheet_id: Annotated[str, Form()] = "",
    notification_local_dir: Annotated[str, Form()] = "",
    daily_report_delivery_hour: Annotated[str, Form()] = "8",
    daily_overview_generation_hour: Annotated[str, Form()] = "7",
    alert_scan_interval_minutes: Annotated[str, Form()] = "30",
) -> Response:
    """フォーム送信を受け取り .env ファイルに書き込む。"""
    form_data = {
        "context_hub_base_url": context_hub_base_url,
        "context_hub_api_key": context_hub_api_key,
        "context_hub_use_mock": context_hub_use_mock,
        "llm_provider": llm_provider,
        "claude_code_cli_path": claude_code_cli_path,
        "notification_channel": notification_channel,
        "slack_bot_token": slack_bot_token,
        "slack_notification_channel": slack_notification_channel,
        "google_service_account_json": google_service_account_json,
        "google_sheet_id": google_sheet_id,
        "notification_local_dir": notification_local_dir,
        "daily_report_delivery_hour": daily_report_delivery_hour,
        "daily_overview_generation_hour": daily_overview_generation_hour,
        "alert_scan_interval_minutes": alert_scan_interval_minutes,
    }

    current = get_settings()

    # バリデーション
    errors: list[str] = []
    ch_err = _validate_channel(notification_channel)
    if ch_err:
        errors.append(ch_err)
    hr_err = _validate_hour(daily_report_delivery_hour, "daily_report_delivery_hour")
    if hr_err:
        errors.append(hr_err)
    gen_err = _validate_hour(
        daily_overview_generation_hour, "daily_overview_generation_hour"
    )
    if gen_err:
        errors.append(gen_err)
    int_err = _validate_interval(alert_scan_interval_minutes)
    if int_err:
        errors.append(int_err)

    if errors:
        banner = f'<div class="banner-err">入力エラー: {"; ".join(errors)}</div>'
        return HTMLResponse(content=_render_page(current, banner), status_code=422)

    # .env 書き込み
    env_path = _resolve_env_path()
    existing_lines = _load_env_lines(env_path)
    updates = _build_updates_from_form(form_data, current)
    new_lines = _update_env_lines(existing_lines, updates)
    env_path.write_text("".join(new_lines), encoding="utf-8")
    logger.info("設定を .env に書き込みました: %s", env_path)

    # キャッシュをクリアして再読み込み
    get_settings.cache_clear()
    updated_settings = get_settings()

    banner = '<div class="banner-ok">設定を保存しました。</div>'
    return HTMLResponse(content=_render_page(updated_settings, banner))


@router.post("/test/context-hub")
async def test_context_hub(
    request: Request,
    context_hub_base_url: Annotated[str, Form()] = "",
    context_hub_api_key: Annotated[str, Form()] = "",
    context_hub_use_mock: Annotated[str, Form()] = "true",
) -> JSONResponse:
    """Context-Hub の /health エンドポイントに GET して到達性を確認する。"""
    current = get_settings()
    api_key_raw = context_hub_api_key
    api_key = current.context_hub_api_key if _is_masked(api_key_raw) else api_key_raw
    base_url = context_hub_base_url or current.context_hub_base_url

    health_url = f"{base_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                health_url,
                headers={"X-Api-Key": api_key},
            )
            resp.raise_for_status()
        return JSONResponse({"ok": True, "detail": f"接続成功 (HTTP {resp.status_code})"})
    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            {"ok": False, "detail": f"HTTP エラー: {exc.response.status_code}"}
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "detail": f"接続失敗: {exc}"})


@router.post("/test/delivery")
async def test_delivery(
    request: Request,
    notification_channel: Annotated[str, Form()] = "",
    slack_bot_token: Annotated[str, Form()] = "",
    slack_notification_channel: Annotated[str, Form()] = "",
    google_service_account_json: Annotated[str, Form()] = "",
    google_sheet_id: Annotated[str, Form()] = "",
    notification_local_dir: Annotated[str, Form()] = "",
) -> JSONResponse:
    """選択された通知チャンネルの healthcheck() を呼んで到達性を確認する。"""
    current = get_settings()

    # チャンネル検証
    channel = notification_channel or current.notification_channel
    ch_err = _validate_channel(channel)
    if ch_err:
        return JSONResponse({"ok": False, "detail": ch_err})

    form_data = {
        "notification_channel": channel,
        "slack_bot_token": slack_bot_token,
        "slack_notification_channel": slack_notification_channel or current.slack_notification_channel,
        "google_service_account_json": google_service_account_json,
        "google_sheet_id": google_sheet_id or current.google_sheet_id,
        "notification_local_dir": notification_local_dir or current.notification_local_dir,
        "context_hub_base_url": current.context_hub_base_url,
        "context_hub_api_key": "••••xxxx",  # masked → keep current
        "context_hub_use_mock": str(current.context_hub_use_mock).lower(),
    }

    temp_settings = _settings_from_form(form_data, current)

    try:
        notifier = build_notifier(temp_settings)
        ok = await notifier.healthcheck()
        if ok:
            return JSONResponse({"ok": True, "detail": f"{channel} への接続に成功しました。"})
        return JSONResponse({"ok": False, "detail": f"{channel} の healthcheck が失敗しました。"})
    except Exception as exc:
        return JSONResponse({"ok": False, "detail": f"エラー: {exc}"})

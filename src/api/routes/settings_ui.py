"""
設定管理 GUI ルーター。

ローカル運用者向けの簡易 Web UI。認証不要（X-Api-Key exempt）。
IMPORTANT: このエンドポイントは localhost 専用のツールです。
           外部公開サーバーでは必ず uvicorn --host 127.0.0.1 で起動してください。

設計:
  - 設定項目は FIELDS（フィールド仕様）で宣言的に定義し、フォーム描画・検証・
    .env 書き込みをすべてそこから導出する（項目追加は FIELDS に 1 行足すだけ）。
  - 各項目には「取得方法 / 説明」のヒントを必ず添える。
  - Settings の全項目を GUI から設定できる（秘匿値は伏字表示・未変更なら現状維持）。

Routes:
  GET  /settings          設定フォームを HTML で返す
  POST /settings          フォーム送信 → .env ファイルに書き込む
  POST /settings/test/context-hub   Context-Hub 接続テスト → JSON
  POST /settings/test/delivery      通知チャンネル接続テスト → JSON
"""

from __future__ import annotations

import html
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from src.application.scheduler.schedule_plan import ScheduleConfig, validate_schedule
from src.config.settings import Settings, get_settings
from src.infrastructure.notifiers.factory import build_notifier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings-ui"])

# ---- 定数 ----------------------------------------------------------------

VALID_CHANNELS = frozenset({"slack", "google_sheets", "local_file", "in_memory"})


# ---- フィールド仕様 ------------------------------------------------------


@dataclass(frozen=True)
class _Field:
    """設定項目 1 つの仕様。name は Settings 属性名＝フォーム name、ENV キーは name.upper()。"""

    name: str
    label: str
    group: str
    kind: str = "text"  # text | number | secret | bool | select
    hint: str = ""  # 取得方法 / 説明（必須運用）
    options: tuple[str, ...] = ()  # kind=select 用
    min: int | None = None  # kind=number 用
    max: int | None = None  # kind=number 用

    @property
    def env(self) -> str:
        return self.name.upper()


# 表示順を兼ねるグループ順
_GROUP_ORDER: tuple[str, ...] = (
    "アプリ基本",
    "データベース",
    "LLM",
    "Context-Hub",
    "通知",
    "スケジュール",
    "セキュリティ",
    "監査ログ",
)

# グループ単位の補足説明（任意）
_GROUP_NOTES: dict[str, str] = {
    "スケジュール": (
        "各フェーズの時刻（時・分）は自由に調整できます。<b>実行順序は時刻設定に関わらず常に固定</b>"
        "（standup → report_generate → report_deliver → report_reminder → wrap_up → alert_scan）で、"
        "複数フェーズを同時刻にしても 1 ジョブにまとめ正しい順に逐次実行します。"
        "リーダー確認後の「全体ステータス分析（final_analysis）」は時刻ではなくリーダーの確認操作で発火します。"
    ),
    "セキュリティ": "このページは localhost 専用です。外部公開時は uvicorn --host 127.0.0.1 で起動してください。",
}

# Settings の全項目を網羅。各項目に取得方法/説明を添える。
FIELDS: tuple[_Field, ...] = (
    # --- アプリ基本 ---
    _Field(
        "app_env",
        "app_env（実行環境）",
        "アプリ基本",
        "select",
        "development | production を選択。production では /docs 等を無効化。",
        options=("development", "production"),
    ),
    _Field(
        "log_level",
        "log_level（ログレベル）",
        "アプリ基本",
        "select",
        "ログ出力レベル。",
        options=("DEBUG", "INFO", "WARNING", "ERROR"),
    ),
    _Field(
        "app_secret_key",
        "app_secret_key（アプリ署名鍵）",
        "アプリ基本",
        "secret",
        "ランダムな文字列。生成例: openssl rand -hex 32",
    ),
    # --- データベース ---
    _Field(
        "use_database",
        "use_database（DB 永続化）",
        "データベース",
        "bool",
        "true=PostgreSQL に永続化（本番。ゲート・日報・タスク等を保存）/ false=インメモリ（dev・テスト）。",
    ),
    _Field(
        "database_url",
        "database_url（接続URL）",
        "データベース",
        "secret",
        "例: postgresql+asyncpg://postgres:〈パスワード〉@db/ai_project_manager。"
        "docker compose の DB 設定（DB_USER/DB_PASSWORD/DB_HOST/DB_NAME）と一致させる。",
    ),
    # --- LLM ---
    _Field(
        "llm_provider",
        "llm_provider",
        "LLM",
        "select",
        "利用する LLM。既定 claude-code（Claude Code CLI 経由＝課金APIゼロ）。",
        options=("claude-code", "codex", "antigravity", "ollama", "mock", "claude"),
    ),
    _Field(
        "claude_code_cli_path",
        "claude_code_cli_path",
        "LLM",
        "text",
        "Claude Code CLI のパス。ターミナルで `which claude` の出力。空なら PATH から自動検出。",
    ),
    _Field(
        "claude_code_timeout_seconds",
        "claude_code_timeout_seconds",
        "LLM",
        "number",
        "Claude Code CLI 呼び出しのタイムアウト秒（既定 120）。",
        min=1,
        max=3600,
    ),
    _Field(
        "anthropic_api_key",
        "anthropic_api_key",
        "LLM",
        "secret",
        "console.anthropic.com → API Keys で発行（sk-ant-…）。llm_provider=claude のときのみ・非推奨。",
    ),
    # --- Context-Hub ---
    _Field(
        "context_hub_base_url",
        "context_hub_base_url",
        "Context-Hub",
        "text",
        "Context-Hub の起動URL（既定 http://localhost:8000/api/v1）。",
    ),
    _Field(
        "context_hub_api_key",
        "context_hub_api_key",
        "Context-Hub",
        "secret",
        "Context-Hub 側の DEV_API_KEY と同値（context-hub の .env もしくは発行コマンドの値）。",
    ),
    _Field(
        "context_hub_use_mock",
        "context_hub_use_mock",
        "Context-Hub",
        "bool",
        "true=モック（Context-Hub 本体なしで動作）/ false=本番接続。",
    ),
    # --- 通知 ---
    _Field(
        "notification_channel",
        "notification_channel（配信先）",
        "通知",
        "select",
        "日報・通知の配信先。未設定時は local_file→in_memory に安全フォールバック。",
        options=("slack", "google_sheets", "local_file", "in_memory"),
    ),
    _Field(
        "slack_bot_token",
        "slack_bot_token",
        "通知",
        "secret",
        "api.slack.com/apps → 対象App → OAuth & Permissions → Bot User OAuth Token（xoxb-…）。"
        "chat:write スコープと、投稿先チャンネルへの Bot 招待が必要。",
    ),
    _Field(
        "slack_notification_channel",
        "slack_notification_channel",
        "通知",
        "text",
        "リーダー向け通知先のチャンネル名（例 #ai-pm-alerts）またはチャンネルID（Cxxxx）。",
    ),
    _Field(
        "google_service_account_json",
        "google_service_account_json",
        "通知",
        "secret",
        "GCP Console → IAMと管理 → サービスアカウント → 鍵を追加(JSON) のファイルパス。"
        "対象スプレッドシートにそのサービスアカウントのメールアドレスを共有しておく。",
    ),
    _Field(
        "google_sheet_id",
        "google_sheet_id",
        "通知",
        "text",
        "スプレッドシートURL …/d/【この部分】/edit を貼り付け。",
    ),
    _Field(
        "notification_local_dir",
        "notification_local_dir",
        "通知",
        "text",
        "local_file 選択時の出力ディレクトリ（既定 ./.ai-pm/notifications）。",
    ),
    # --- スケジュール ---
    _Field(
        "scheduler_enabled",
        "scheduler_enabled（自動スケジューラ）",
        "スケジュール",
        "bool",
        "有効=設定時刻に自動実行 / 無効=手動 API 実行のみ。",
    ),
    _Field(
        "scheduler_timezone",
        "scheduler_timezone",
        "スケジュール",
        "text",
        "cron 時刻の解釈に使う IANA タイムゾーン名（例 Asia/Tokyo）。"
        "一覧は zoneinfo.available_timezones() で確認可。",
    ),
    _Field(
        "standup_hour",
        "朝会（スタンドアップ）の時",
        "スケジュール",
        "number",
        "0〜23。前日レビュー＋アサイン点検を共有する時刻。",
        min=0,
        max=23,
    ),
    _Field("standup_minute", "朝会の分", "スケジュール", "number", "0〜59。", min=0, max=59),
    _Field("report_hour", "日報 生成→配信の時", "スケジュール", "number", "0〜23。", min=0, max=23),
    _Field("report_minute", "日報の分", "スケジュール", "number", "0〜59。", min=0, max=59),
    _Field(
        "reminder_hour", "日報未提出 催促の時", "スケジュール", "number", "0〜23。", min=0, max=23
    ),
    _Field("reminder_minute", "催促の分", "スケジュール", "number", "0〜59。", min=0, max=59),
    _Field(
        "wrap_up_hour",
        "当日総括＋確認ゲートの時",
        "スケジュール",
        "number",
        "0〜23。",
        min=0,
        max=23,
    ),
    _Field("wrap_up_minute", "総括の分", "スケジュール", "number", "0〜59。", min=0, max=59),
    _Field(
        "alert_scan_interval_minutes",
        "alert_scan_interval_minutes（アラート間隔・分）",
        "スケジュール",
        "number",
        "1〜1440。短くしても多重起動・滞留しない。",
        min=1,
        max=1440,
    ),
    # --- セキュリティ ---
    _Field(
        "cors_origins",
        "cors_origins",
        "セキュリティ",
        "text",
        "ブラウザから叩くフロントのオリジンをカンマ区切りで（例 http://localhost:3000）。",
    ),
    _Field(
        "jwt_secret",
        "jwt_secret",
        "セキュリティ",
        "secret",
        "JWT 署名用のランダム文字列。生成例: openssl rand -hex 32",
    ),
    _Field(
        "jwt_expiry_hours",
        "jwt_expiry_hours",
        "セキュリティ",
        "number",
        "発行する JWT の有効期限（時間）。",
        min=1,
        max=720,
    ),
    # --- 監査ログ ---
    _Field(
        "audit_log_retention_days",
        "audit_log_retention_days",
        "監査ログ",
        "number",
        "監査ログの保持日数（既定 365）。",
        min=1,
        max=3650,
    ),
    _Field(
        "audit_log_dir",
        "audit_log_dir",
        "監査ログ",
        "text",
        "監査ログ(JSONL)の出力ディレクトリ。空ならインメモリ（dev・テスト）。",
    ),
)

_FIELD_BY_NAME: dict[str, _Field] = {f.name: f for f in FIELDS}
_SECRET_FIELDS = frozenset(f.name for f in FIELDS if f.kind == "secret")

# スケジュール各フェーズ: (フィールド接頭辞, 日本語ラベル)
_SCHEDULE_PHASES: tuple[tuple[str, str], ...] = (
    ("standup", "スタンドアップ"),
    ("report", "日報生成→配信"),
    ("reminder", "日報未提出の催促"),
    ("wrap_up", "当日総括＋確認ゲート"),
)

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
        return (
            f"無効な notification_channel: '{channel}'. 選択肢: {', '.join(sorted(VALID_CHANNELS))}"
        )
    return None


def _validate_number(value: str, f: _Field) -> str | None:
    """number 項目の整数・範囲を検証。"""
    try:
        n = int(value)
    except ValueError:
        return f"{f.label} は整数で指定してください。"
    if f.min is not None and n < f.min:
        return f"{f.label} は {f.min} 以上で指定してください。"
    if f.max is not None and n > f.max:
        return f"{f.label} は {f.max} 以下で指定してください。"
    return None


def _validate_form(form_data: dict[str, str]) -> list[str]:
    """送信された（＝存在する）項目のみ仕様に沿って検証する。"""
    errors: list[str] = []
    for f in FIELDS:
        if f.name not in form_data:
            continue
        value = form_data[f.name]
        if f.kind == "number":
            err = _validate_number(value, f)
            if err:
                errors.append(err)
        elif f.kind == "select":
            if value not in f.options:
                errors.append(
                    f"{f.label} の値 '{value}' は無効です。選択肢: {', '.join(f.options)}"
                )
        elif f.kind == "bool":
            if value not in ("true", "false"):
                errors.append(f"{f.label} は true / false で指定してください。")
    return errors


# ---- HTML 描画 -----------------------------------------------------------

# 通知/LLM の選択に応じて表示する条件付き項目: name -> (制御フィールド, 表示する値)
_SHOW_WHEN: dict[str, tuple[str, tuple[str, ...]]] = {
    "slack_bot_token": ("notification_channel", ("slack",)),
    "google_service_account_json": ("notification_channel", ("google_sheets",)),
    "google_sheet_id": ("notification_channel", ("google_sheets",)),
    "notification_local_dir": ("notification_channel", ("local_file",)),
    "anthropic_api_key": ("llm_provider", ("claude",)),
}

# スケジュールの時刻ペア: hour フィールド名 -> (minute フィールド名, ラベル)
_TIME_PAIRS: dict[str, tuple[str, str]] = {
    "standup_hour": ("standup_minute", "朝会（スタンドアップ）"),
    "report_hour": ("report_minute", "日報 生成→配信"),
    "reminder_hour": ("reminder_minute", "日報未提出 催促"),
    "wrap_up_hour": ("wrap_up_minute", "当日総括＋確認ゲート"),
}
_TIME_MINUTES: frozenset[str] = frozenset(m for m, _ in _TIME_PAIRS.values())

# 認証・接続系の項目について「いつ必要 / なぜ必要 / 取得手順 / どう設定」を提供する。
# tag=いつ必要か（バッジ）, why=理由, steps=取得手順, set=この欄への入れ方。
_GUIDES: dict[str, dict] = {
    "context_hub_base_url": {
        "tag": "本番連携時",
        "why": "AI-PM は会議メモ・課題などの「文脈」を Context-Hub から取得します。その接続先URLです。",
        "steps": [
            "別途 Context-Hub を起動します（同じPCなら http://localhost:8000 ）。",
            "API のベースは末尾に /api/v1 を付けた http://localhost:8000/api/v1 です。",
        ],
        "set": "上記URLを入力。まだ Context-Hub を使わないなら context_hub_use_mock=true にすれば本欄は不要です。",
    },
    "context_hub_api_key": {
        "tag": "本番連携時",
        "why": "Context-Hub は API キー認証（X-Api-Key）のため、AI-PM が文脈を取りに行く際にキーが要ります。"
        "このキーは『どこかから貰う』のではなく、開発時は『自分で決めて両者を一致させる』値です。",
        "steps": [
            "開発時は好きな文字列を自分で決めます（例: dev-seed-key）。",
            "その値を環境変数 DEV_API_KEY にして Context-Hub を起動: "
            "DEV_API_KEY=dev-seed-key APP_ENV=development uvicorn context_hub.main:create_app --factory --host 127.0.0.1 --port 8000",
            "Context-Hub は開発時、X-Api-Key がこの DEV_API_KEY と一致すれば通します（＝決めた値がそのまま API キー）。",
            "本番（APP_ENV=production）では DEV_API_KEY は無効。Context-Hub 側で発行したコンシューマキーを使います。",
        ],
        "set": "手順2で決めた値（例 dev-seed-key）と同じものを「変更」を押して入力。use_mock=true の間は不要。"
        "詳しくは運用ガイド（右上）§4。",
    },
    "slack_bot_token": {
        "tag": "Slack配信時",
        "why": "日報・アラート・スタンドアップ等を Slack に送るためのBotトークンです（配信先=slack のとき）。",
        "steps": [
            "https://api.slack.com/apps を開き「Create New App」→「From scratch」→ ワークスペースを選択。",
            "左メニュー「OAuth & Permissions」→ Scopes →「Bot Token Scopes」に chat:write を追加。",
            "同ページ上部「Install to Workspace」→ 許可。",
            "表示される「Bot User OAuth Token」（xoxb- で始まる）をコピー。",
            "通知したいチャンネルで /invite @アプリ名 を実行し、Botを招待。",
        ],
        "set": "「変更」を押して xoxb-… を貼り付け → 保存。",
    },
    "slack_notification_channel": {
        "tag": "通知の宛先",
        "why": "リーダー向け通知（アラート・未提出一覧・総括）を送るチャンネルです。",
        "steps": [
            "通常はチャンネル名（例 #ai-pm-alerts）でOK。",
            "ID が必要な場合：Slack でチャンネル名をクリック →「チャンネル詳細」最下部の Channel ID（Cxxxx）。",
        ],
        "set": "#チャンネル名 または Cxxxx を入力。",
    },
    "google_service_account_json": {
        "tag": "Sheets配信時",
        "why": "Google スプレッドシートに書き込むための認証情報です（配信先=google_sheets のとき）。",
        "steps": [
            "https://console.cloud.google.com でプロジェクトを作成/選択。",
            "「APIとサービス」→「ライブラリ」→ Google Sheets API を有効化。",
            "「IAMと管理」→「サービスアカウント」→ 作成。",
            "作成したサービスアカウント →「キー」→「鍵を追加」→ JSON を選びダウンロード。",
            "その JSON ファイルを社内PCの安全な場所に保存。",
            "対象スプレッドシートを開き「共有」で、サービスアカウントのメール（…@….iam.gserviceaccount.com）に編集権限で共有。",
        ],
        "set": "「変更」を押して JSON ファイルのパスを入力。",
    },
    "google_sheet_id": {
        "tag": "Sheets配信時",
        "why": "どのスプレッドシートに書き込むかの指定です。",
        "steps": [
            "対象シートを開き、URL https://docs.google.com/spreadsheets/d/【ここがID】/edit の【】部分をコピー。",
        ],
        "set": "その ID を貼り付け。",
    },
    "anthropic_api_key": {
        "tag": "claude時のみ",
        "why": "llm_provider=claude（課金API）を使う場合のみ必要。既定の claude-code では不要です。",
        "steps": [
            "https://console.anthropic.com →「API Keys」→「Create Key」で発行（sk-ant-… で始まる）。",
        ],
        "set": "「変更」を押して貼り付け。通常は claude-code 推奨で本欄は空でOK。",
    },
    "database_url": {
        "tag": "本番DB時",
        "why": "タスク・日報・リーダー確認ゲート等の保存先 PostgreSQL です（use_database=true のとき）。",
        "steps": [
            "形式は postgresql+asyncpg://〈ユーザー〉:〈パスワード〉@〈ホスト〉/〈DB名〉。",
            "docker compose を使うなら .env の DB_USER / DB_PASSWORD / DB_HOST(=db) / DB_NAME と一致させます。",
        ],
        "set": "「変更」を押して URL を貼り付け。compose 既定なら postgresql+asyncpg://postgres:〈DB_PASSWORD〉@db/ai_project_manager。",
    },
    "app_secret_key": {
        "tag": "本番は必須",
        "why": "アプリの署名に使う秘密鍵。漏れると改ざんされ得るのでランダム値にします。"
        "なお、この値は AI-PM の業務API（/api/v1/...）を叩くときの X-Api-Key としても使います。",
        "steps": [
            "ターミナルで openssl rand -hex 32 を実行し、出力をコピー。",
        ],
        "set": "「変更」を押して貼り付け（本番では既定値のままにしない）。API を叩く際は同じ値を X-Api-Key ヘッダーに入れます。",
    },
    "jwt_secret": {
        "tag": "本番は必須",
        "why": "ログイン用 JWT の署名鍵。app_secret_key と同様にランダム値にします。",
        "steps": [
            "ターミナルで openssl rand -hex 32 を実行し、出力をコピー。",
        ],
        "set": "「変更」を押して貼り付け。",
    },
}

# はじめに（最小構成と「何を設定すればいいか」の早見表）
_INTRO = """
<section class="intro">
<h2>はじめに — 何を設定すればいい？</h2>
<p>外部連携の多くは<b>任意</b>です。まずは外部トークンなしで動かせます。やりたいことに応じて下表の項目だけ設定してください。各項目の「<b>なぜ必要？ 取得・設定の手順</b>」を開くと、取得元のURLと手順が出ます。</p>
<table>
<tr><th>やりたいこと</th><th>設定する項目</th></tr>
<tr><td>まず動かす（外部トークン不要）</td><td><code>notification_channel = local_file</code> ＋ <code>context_hub_use_mock = true</code></td></tr>
<tr><td>Slack に通知したい</td><td>＋ <code>notification_channel = slack</code> ／ <code>slack_bot_token</code> ／ <code>slack_notification_channel</code></td></tr>
<tr><td>Google スプレッドシートに記録</td><td>＋ <code>notification_channel = google_sheets</code> ／ <code>google_service_account_json</code> ／ <code>google_sheet_id</code></td></tr>
<tr><td>実データの文脈連携（Context-Hub）</td><td><code>context_hub_use_mock = false</code> ／ <code>context_hub_base_url</code> ／ <code>context_hub_api_key</code></td></tr>
<tr><td>本番DBに永続化</td><td><code>use_database = true</code> ／ <code>database_url</code></td></tr>
</table>
</section>
"""

# ブランド統一（docs と同じ 黒・白・紅 / 余白 / 明朝見出し）。CSS/JS は f-string ではない。
_HEAD = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI-Project-Manager 設定</title>
<style>
  :root{
    --ink:#0b0b0c;--ink-soft:#2a2a2e;--paper:#fbfaf8;--paper-2:#f2efea;--line:#e4ded5;
    --crimson:#b51b2e;--crimson-d:#8c1322;--muted:#6f6a62;--muted-2:#9a948b;--ok:#0a7a3d;
    --serif:"Hiragino Mincho ProN","Yu Mincho","Noto Serif JP",Georgia,serif;
    --sans:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP","Segoe UI",sans-serif;
    --mono:"SF Mono","JetBrains Mono","Roboto Mono",monospace;
  }
  *{box-sizing:border-box;}
  html{scroll-behavior:smooth;}
  body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.7;
    -webkit-font-smoothing:antialiased;}
  ::selection{background:var(--crimson);color:#fff;}
  .wrap{max-width:780px;margin:0 auto;padding:18px 22px 130px;}

  header.top{position:sticky;top:0;z-index:30;background:rgba(251,250,248,.93);
    backdrop-filter:saturate(140%) blur(10px);border-bottom:1px solid var(--line);}
  header.top .inner{max-width:780px;margin:0 auto;padding:14px 22px 0;}
  header.top h1{font-family:var(--serif);font-weight:600;font-size:1.3rem;margin:0;
    padding-left:14px;position:relative;letter-spacing:.02em;}
  header.top h1::before{content:"";position:absolute;left:0;top:4px;bottom:4px;width:4px;background:var(--crimson);}
  header.top .sub{font-size:.76rem;color:var(--muted);margin:5px 0 10px;}
  nav.jump{display:flex;gap:1px;flex-wrap:wrap;overflow-x:auto;}
  nav.jump a{font-size:.78rem;color:var(--muted);padding:7px 11px;white-space:nowrap;
    text-decoration:none;border-bottom:2px solid transparent;cursor:pointer;}
  nav.jump a:hover{color:var(--ink);}
  nav.jump a.active{color:var(--crimson-d);border-bottom-color:var(--crimson);}

  fieldset{border:1px solid var(--line);border-radius:3px;padding:6px 20px 14px;margin:18px 0;
    background:#fff;scroll-margin-top:104px;}
  legend{font-family:var(--serif);font-weight:600;font-size:1.04rem;padding:0 8px;}
  .grp-note{font-size:.78rem;color:var(--muted);margin:6px 0 4px;}

  .field{padding:12px 0;border-top:1px solid var(--paper-2);}
  .field:first-of-type{border-top:0;}
  .field.field-hidden{display:none;}
  .fhead{display:flex;align-items:baseline;justify-content:space-between;gap:10px;}
  label{font-size:.85rem;font-weight:600;}
  .secret-tag{font-weight:400;color:var(--muted-2);font-size:.7rem;}
  .ftools{display:flex;gap:12px;align-items:center;flex:none;}
  .link{background:none;border:0;color:var(--crimson-d);font-size:.72rem;cursor:pointer;padding:0;
    text-decoration:underline;font-family:var(--sans);}
  .chg{display:none;font-size:.68rem;color:var(--crimson-d);white-space:nowrap;}
  .field.changed .chg{display:inline;}
  input[type=text],input[type=number],select{width:100%;margin-top:6px;padding:8px 10px;
    border:1px solid var(--line);border-radius:3px;font-size:.9rem;background:var(--paper);
    font-family:var(--sans);color:var(--ink);}
  input:focus,select:focus{outline:none;border-color:var(--ink);background:#fff;}
  input[readonly]{color:var(--muted);background:var(--paper-2);}
  .field.changed input,.field.changed select{border-color:var(--crimson);}
  .trow{display:flex;align-items:center;gap:9px;margin-top:6px;}
  .trow input{width:84px;margin-top:0;text-align:center;}
  .colon{font-weight:700;color:var(--muted);}
  .hint{font-size:.745rem;color:var(--muted);margin:5px 0 0;}
  .ferr{font-size:.745rem;color:var(--crimson-d);margin:4px 0 0;}

  form .actions{position:fixed;left:0;right:0;bottom:0;z-index:20;background:rgba(251,250,248,.96);
    backdrop-filter:blur(10px);border-top:1px solid var(--line);padding:11px 22px;}
  .actions .inner{max-width:780px;margin:0 auto;display:flex;gap:10px;flex-wrap:wrap;align-items:center;}
  button.save{background:var(--ink);color:#fff;border:0;padding:10px 28px;border-radius:3px;
    font-size:.88rem;cursor:pointer;letter-spacing:.04em;}
  button.save:hover{background:var(--crimson-d);}
  .test-btn{background:#fff;color:var(--ink-soft);border:1px solid var(--line);padding:8px 15px;
    border-radius:3px;font-size:.82rem;cursor:pointer;}
  .test-btn:hover{border-color:var(--ink);}
  .results{flex:1 1 100%;display:flex;gap:18px;font-size:.78rem;margin-top:2px;}
  .banner-ok,.banner-err{background:#fff;border:1px solid var(--line);border-left:3px solid var(--crimson);
    padding:11px 15px;border-radius:3px;margin:14px 0;font-size:.88rem;}
  .banner-err{color:var(--crimson-d);}
  .ok{color:var(--ok);} .err{color:var(--crimson-d);}

  /* はじめに（早見表） */
  .intro{background:#fff;border:1px solid var(--line);border-left:3px solid var(--crimson);
    border-radius:3px;padding:16px 18px;margin:18px 0;}
  .intro h2{font-family:var(--serif);font-size:1.06rem;margin:0 0 6px;}
  .intro p{font-size:.82rem;color:var(--ink-soft);margin:0 0 10px;}
  .intro table{width:100%;border-collapse:collapse;font-size:.8rem;}
  .intro th,.intro td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--paper-2);vertical-align:top;}
  .intro th{color:var(--muted);font-weight:700;font-size:.72rem;letter-spacing:.04em;}
  code{font-family:var(--mono);font-size:.86em;background:var(--paper-2);padding:1px 5px;border-radius:3px;color:var(--crimson-d);}

  /* いつ必要かバッジ */
  .badge{display:inline-block;font-size:.64rem;font-weight:700;letter-spacing:.03em;
    border:1px solid var(--line);border-radius:100px;padding:1px 8px;color:var(--muted);
    margin-left:8px;vertical-align:middle;white-space:nowrap;}

  /* 取得手順（折りたたみ） */
  details.guide{margin:7px 0 0;border:1px solid var(--line);border-radius:3px;background:var(--paper);}
  details.guide>summary{cursor:pointer;font-size:.745rem;color:var(--crimson-d);padding:7px 11px;list-style:none;}
  details.guide>summary::-webkit-details-marker{display:none;}
  details.guide>summary::before{content:"▸ ";}
  details.guide[open]>summary::before{content:"▾ ";}
  details.guide[open]>summary{border-bottom:1px solid var(--line);font-weight:700;}
  .gbody{padding:11px 13px;font-size:.785rem;color:var(--ink-soft);line-height:1.8;}
  .gbody b{color:var(--ink);}
  .gbody ol{margin:7px 0;padding-left:20px;}
  .gbody li{margin:4px 0;}
  .gbody a{color:var(--crimson-d);word-break:break-all;}
</style>
</head>
<body>
"""

_SCRIPT = """
<script>
(function(){
  var form = document.querySelector('form');

  function fieldOf(el){ return el.closest ? el.closest('.field') : null; }

  function markChanged(el){
    var f = fieldOf(el); if(!f) return;
    var init = el.dataset.initial != null ? el.dataset.initial : '';
    f.classList.toggle('changed', el.value !== init);
  }

  function validateNumber(el){
    var f = fieldOf(el); if(!f) return true;
    var err = f.querySelector('.ferr'); var msg = '';
    if(el.value !== ''){
      var n = Number(el.value);
      if(!Number.isInteger(n)) msg = '整数で入力してください。';
      else if(el.min !== '' && n < Number(el.min)) msg = el.min + ' 以上にしてください。';
      else if(el.max !== '' && n > Number(el.max)) msg = el.max + ' 以下にしてください。';
    }
    if(err) err.textContent = msg;
    return msg === '';
  }

  function applyConditional(){
    document.querySelectorAll('[data-show-when]').forEach(function(f){
      var ctrl = document.getElementById(f.dataset.showWhen);
      var vals = (f.dataset.showVals || '').split('|');
      var show = ctrl && vals.indexOf(ctrl.value) >= 0;
      f.classList.toggle('field-hidden', !show);
    });
  }

  form.addEventListener('input', function(e){
    var el = e.target;
    markChanged(el);
    if(el.type === 'number') validateNumber(el);
    if(el.id === 'notification_channel' || el.id === 'llm_provider') applyConditional();
  });
  form.addEventListener('change', function(e){ markChanged(e.target); });

  document.querySelectorAll('[data-reset]').forEach(function(btn){
    btn.addEventListener('click', function(){
      var el = document.getElementById(btn.dataset.reset); if(!el) return;
      el.value = el.dataset.default != null ? el.dataset.default : '';
      el.dispatchEvent(new Event('input', {bubbles:true}));
    });
  });

  document.querySelectorAll('[data-secret-edit]').forEach(function(btn){
    var el = document.getElementById(btn.dataset.secretEdit); if(!el) return;
    btn.addEventListener('click', function(){
      if(el.hasAttribute('readonly')){
        el.removeAttribute('readonly'); el.value = ''; el.placeholder = '新しい値を入力（保存で更新）'; el.focus();
        btn.textContent = '取消';
      } else {
        el.setAttribute('readonly','readonly'); el.value = el.dataset.initial || '';
        el.placeholder = ''; btn.textContent = '変更';
      }
      markChanged(el);
    });
  });

  form.addEventListener('submit', function(e){
    var ok = true;
    form.querySelectorAll('input[type=number]').forEach(function(el){ if(!validateNumber(el)) ok = false; });
    if(!ok){
      e.preventDefault();
      var first = form.querySelector('.ferr');
      var nodes = form.querySelectorAll('.ferr');
      for(var i=0;i<nodes.length;i++){ if(nodes[i].textContent){ nodes[i].scrollIntoView({block:'center'}); break; } }
    }
  });

  var links = Array.prototype.slice.call(document.querySelectorAll('nav.jump a'));
  if(window.IntersectionObserver){
    var obs = new IntersectionObserver(function(entries){
      entries.forEach(function(en){
        if(en.isIntersecting){
          links.forEach(function(a){ a.classList.toggle('active', a.dataset.nav === en.target.id); });
        }
      });
    }, {rootMargin:'-45% 0px -50% 0px'});
    document.querySelectorAll('fieldset[id]').forEach(function(fs){ obs.observe(fs); });
  }

  applyConditional();
})();

async function testHub(){
  var el = document.getElementById('test-hub-result');
  el.textContent = '確認中…';
  var data = new FormData(document.querySelector('form'));
  try {
    var res = await fetch('/settings/test/context-hub', { method:'POST', body:data });
    var json = await res.json();
    el.textContent = json.ok ? '✓ ' + json.detail : '✗ ' + json.detail;
    el.className = json.ok ? 'ok' : 'err';
  } catch(e){ el.textContent = '✗ ' + e; el.className = 'err'; }
}
async function testDelivery(){
  var el = document.getElementById('test-delivery-result');
  el.textContent = '確認中…';
  var data = new FormData(document.querySelector('form'));
  try {
    var res = await fetch('/settings/test/delivery', { method:'POST', body:data });
    var json = await res.json();
    el.textContent = json.ok ? '✓ ' + json.detail : '✗ ' + json.detail;
    el.className = json.ok ? 'ok' : 'err';
  } catch(e){ el.textContent = '✗ ' + e; el.className = 'err'; }
}
</script>
</body>
</html>"""


def _field_value(settings: Settings, f: _Field) -> str:
    """現在の設定値を表示用文字列に変換する（秘匿はマスク・bool は true/false）。"""
    raw = getattr(settings, f.name)
    if f.kind == "secret":
        return _mask_secret(str(raw or ""))
    if f.kind == "bool":
        return "true" if bool(raw) else "false"
    return str(raw)


def _field_default(f: _Field) -> str:
    """Settings の宣言済み既定値を表示用文字列で返す（「既定値に戻す」用）。"""
    default = Settings.model_fields[f.name].default
    if f.kind == "bool":
        return "true" if bool(default) else "false"
    return "" if default is None else str(default)


def _input_html(settings: Settings, f: _Field) -> str:
    """入力要素のみを描画する（data-initial / data-default 付き）。"""
    val = _field_value(settings, f)
    init = f' data-initial="{html.escape(val)}"'
    dflt = "" if f.kind == "secret" else f' data-default="{html.escape(_field_default(f))}"'
    common = f'id="{f.name}" name="{f.name}"{init}{dflt}'

    if f.kind == "bool":
        sel_t = "selected" if val == "true" else ""
        sel_f = "selected" if val == "false" else ""
        return (
            f"<select {common}>"
            f'<option value="true" {sel_t}>true（有効）</option>'
            f'<option value="false" {sel_f}>false（無効）</option>'
            f"</select>"
        )
    if f.kind == "select":
        opts = "".join(
            f'<option value="{html.escape(o)}" {"selected" if o == val else ""}>{html.escape(o)}</option>'
            for o in f.options
        )
        return f"<select {common}>{opts}</select>"
    if f.kind == "number":
        mn = f' min="{f.min}"' if f.min is not None else ""
        mx = f' max="{f.max}"' if f.max is not None else ""
        return f'<input type="number" {common}{mn}{mx} value="{html.escape(val)}">'
    if f.kind == "secret":
        return (
            f'<input type="text" {common} value="{html.escape(val)}" readonly '
            f'placeholder="（未変更：現在値を維持）">'
        )
    return f'<input type="text" {common} value="{html.escape(val)}">'


def _show_when_attr(f: _Field) -> str:
    rule = _SHOW_WHEN.get(f.name)
    if not rule:
        return ""
    controller, values = rule
    return f' data-show-when="{controller}" data-show-vals="{"|".join(values)}"'


def _linkify(text: str) -> str:
    """テキストを HTML エスケープし、http(s) URL を安全にリンク化する。"""
    escaped = html.escape(text)
    return re.sub(
        r"(https?://[^\s）)、。]+)",
        r'<a href="\1" target="_blank" rel="noopener">\1</a>',
        escaped,
    )


def _render_guide(name: str) -> str:
    """「なぜ必要 / 取得手順 / どう設定」の折りたたみを描画する（無ければ空）。"""
    g = _GUIDES.get(name)
    if not g:
        return ""
    steps = "".join(f"<li>{_linkify(s)}</li>" for s in g.get("steps", ()))
    return (
        '<details class="guide"><summary>なぜ必要？ 取得・設定の手順</summary>'
        '<div class="gbody">'
        f"<p><b>なぜ必要</b>：{_linkify(g['why'])}</p>"
        f"<ol>{steps}</ol>"
        f"<p><b>ここに設定</b>：{_linkify(g['set'])}</p>"
        "</div></details>"
    )


def _badge(name: str) -> str:
    """「いつ必要か」バッジ（ガイドの tag）。"""
    g = _GUIDES.get(name)
    if g and g.get("tag"):
        return f'<span class="badge">{html.escape(g["tag"])}</span>'
    return ""


def _render_field_block(settings: Settings, f: _Field) -> str:
    """1 項目（ラベル＋ツール＋入力＋ヒント＋取得手順＋エラー枠）を描画する。"""
    if f.kind == "secret":
        tag = ' <span class="secret-tag">(シークレット・伏字)</span>'
        tools = (
            f'<button type="button" class="link" data-secret-edit="{f.name}">変更</button>'
            '<span class="chg">● 変更あり</span>'
        )
    else:
        tag = ""
        tools = (
            f'<button type="button" class="link" data-reset="{f.name}">既定値に戻す</button>'
            '<span class="chg">● 変更あり</span>'
        )
    head = (
        f'<div class="fhead"><label for="{f.name}">{html.escape(f.label)}{tag}{_badge(f.name)}</label>'
        f'<span class="ftools">{tools}</span></div>'
    )
    hint = f'<p class="hint">{f.hint}</p>' if f.hint else ""
    return (
        f'<div class="field" data-name="{f.name}"{_show_when_attr(f)}>'
        f'{head}{_input_html(settings, f)}{hint}{_render_guide(f.name)}<p class="ferr"></p></div>'
    )


def _render_time_pair(settings: Settings, hour_f: _Field, minute_f: _Field, label: str) -> str:
    """時・分を横並び（時 : 分）で描画する。"""
    h = _input_html(settings, hour_f)
    m = _input_html(settings, minute_f)
    head = (
        f'<div class="fhead"><label>{html.escape(label)}（時 : 分）</label>'
        f'<span class="ftools">'
        f'<button type="button" class="link" data-reset="{hour_f.name}">時を既定へ</button>'
        f'<button type="button" class="link" data-reset="{minute_f.name}">分を既定へ</button>'
        f'<span class="chg">● 変更あり</span></span></div>'
    )
    return (
        f'<div class="field" data-name="{hour_f.name}">{head}'
        f'<div class="trow">{h}<span class="colon">:</span>{m}</div>'
        f'<p class="hint">時 0〜23 ／ 分 0〜59</p><p class="ferr"></p></div>'
    )


def _render_fields(settings: Settings) -> str:
    grouped: dict[str, list[_Field]] = {}
    for f in FIELDS:
        grouped.setdefault(f.group, []).append(f)

    blocks: list[str] = []
    for idx, group in enumerate(_GROUP_ORDER):
        fields = grouped.get(group, [])
        if not fields:
            continue
        blocks.append(f'<fieldset id="grp-{idx}"><legend>{html.escape(group)}</legend>')
        if group in _GROUP_NOTES:
            blocks.append(f'<p class="grp-note">{_GROUP_NOTES[group]}</p>')
        for f in fields:
            if f.name in _TIME_MINUTES:
                continue  # 時刻ペアとして hour 側でまとめて描画
            if f.name in _TIME_PAIRS:
                minute_name, label = _TIME_PAIRS[f.name]
                blocks.append(_render_time_pair(settings, f, _FIELD_BY_NAME[minute_name], label))
                continue
            blocks.append(_render_field_block(settings, f))
        blocks.append("</fieldset>")
    return "\n".join(blocks)


def _render_nav() -> str:
    links = "".join(
        f'<a href="#grp-{i}" data-nav="grp-{i}">{html.escape(g)}</a>'
        for i, g in enumerate(_GROUP_ORDER)
    )
    return (
        '<header class="top"><div class="inner">'
        "<h1>AI-Project-Manager 設定 "
        '<a href="/register" style="font-size:.8rem;font-weight:400;margin-left:10px;'
        'color:var(--crimson-d);text-decoration:underline;">🗂 プロジェクト/メンバー登録</a>'
        '<a href="/guide" style="font-size:.8rem;font-weight:400;margin-left:10px;'
        'color:var(--crimson-d);text-decoration:underline;">📖 運用ガイド（はじめての方へ）</a></h1>'
        '<div class="sub">ローカル管理者専用。保存で .env に書き込み＋即リロード。'
        "各項目に取得方法のヒント付き。秘匿値は「変更」を押すと編集できます。"
        "「何をどう設定すればいいか分からない」ときは右上の運用ガイドへ。</div>"
        f'<nav class="jump">{links}</nav>'
        "</div></header>"
    )


_ACTIONS = """
  <div class="actions"><div class="inner">
    <button type="submit" class="save">保存</button>
    <button type="button" class="test-btn" onclick="testHub()">Context-Hub 接続テスト</button>
    <button type="button" class="test-btn" onclick="testDelivery()">通知チャンネル テスト</button>
    <div class="results"><span id="test-hub-result"></span><span id="test-delivery-result"></span></div>
  </div></div>
</form>
"""


def _render_page(settings: Settings, banner: str = "") -> str:
    """設定値を HTML に描画して返す。"""
    return (
        _HEAD
        + _render_nav()
        + '<div class="wrap">'
        + banner
        + _INTRO
        + '<form method="POST" action="/settings">\n'
        + _render_fields(settings)
        + _ACTIONS
        + "</div>"
        + _SCRIPT
    )


# ---- フォーム送信処理 ---------------------------------------------------


def _build_updates_from_form(form_data: dict[str, str], current: Settings) -> dict[str, str]:
    """
    送信された（＝存在する）項目のみを .env 書き込み用の更新辞書に変換する。

    - 秘匿項目が空 / マスク済みなら現在値を維持
    - 未送信の項目は対象外（既存 .env 行はそのまま保持される）
    """
    updates: dict[str, str] = {}
    for f in FIELDS:
        if f.name not in form_data:
            continue
        submitted = form_data[f.name]
        if f.kind == "secret":
            current_val = str(getattr(current, f.name) or "")
            updates[f.env] = current_val if _is_masked(submitted) else submitted
        else:
            updates[f.env] = submitted
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
        context_hub_base_url=form_data.get("context_hub_base_url", current.context_hub_base_url),
        context_hub_api_key=hub_key,
        context_hub_use_mock=(form_data.get("context_hub_use_mock", "true").lower() == "true"),
        llm_provider=form_data.get("llm_provider", current.llm_provider),
        claude_code_cli_path=form_data.get("claude_code_cli_path", current.claude_code_cli_path),
        notification_channel=form_data.get("notification_channel", current.notification_channel),
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
async def post_settings(request: Request) -> Response:
    """フォーム送信を受け取り .env ファイルに書き込む。

    送信された項目のみを更新し、未送信項目の既存 .env 行は保持する。
    """
    raw_form = await request.form()
    form_data = {k: str(v) for k, v in raw_form.items() if k in _FIELD_BY_NAME}

    current = get_settings()

    errors = _validate_form(form_data)
    if errors:
        banner = f'<div class="banner-err">入力エラー: {"; ".join(errors)}</div>'
        return HTMLResponse(content=_render_page(current, banner), status_code=422)

    # .env 書き込み
    env_path = _resolve_env_path()
    existing_lines = _load_env_lines(env_path)
    updates = _build_updates_from_form(form_data, current)
    new_lines = _update_env_lines(existing_lines, updates)
    env_path.write_text("".join(new_lines), encoding="utf-8")
    logger.info("設定を .env に書き込みました: %s（%d 項目）", env_path, len(updates))

    # キャッシュをクリアして再読み込み
    get_settings.cache_clear()
    updated = get_settings()

    # 範囲は検証済み。運用上の注意（同時刻など）を warning として表示する。
    validation = validate_schedule(
        ScheduleConfig(
            standup_hour=updated.standup_hour,
            standup_minute=updated.standup_minute,
            report_hour=updated.report_hour,
            report_minute=updated.report_minute,
            reminder_hour=updated.reminder_hour,
            reminder_minute=updated.reminder_minute,
            wrap_up_hour=updated.wrap_up_hour,
            wrap_up_minute=updated.wrap_up_minute,
            scan_interval_minutes=updated.alert_scan_interval_minutes,
        )
    )
    banner = '<div class="banner-ok">設定を保存しました。</div>'
    for warning in validation.warnings:
        banner += f'<div class="banner-err" style="background:#fef3c7;color:#92400e;">注意: {warning}</div>'
    return HTMLResponse(content=_render_page(updated, banner))


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
        return JSONResponse({"ok": False, "detail": f"HTTP エラー: {exc.response.status_code}"})
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
        "slack_notification_channel": slack_notification_channel
        or current.slack_notification_channel,
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

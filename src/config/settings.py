"""
アプリケーション設定管理。
pydantic-settings で .env から読み込む。
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- アプリ基本設定 ---
    app_env: str = Field(default="development")
    app_secret_key: str = Field(default="dev-secret-change-in-production")
    log_level: str = Field(default="INFO")

    # --- DB ---
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5433/ai_project_manager"
    )
    # True で SqlAlchemy リポジトリを使用、False でインメモリ（dev / 単体テスト用）
    use_database: bool = Field(default=False)

    # --- LLM ---
    # 2026-05-15: claude-code をデフォルトに変更（課金 API ゼロ方針）
    # 対応: claude-code | codex | antigravity | ollama | mock
    # 非推奨: claude（課金 API）
    llm_provider: str = Field(default="claude-code")
    anthropic_api_key: str = Field(default="")  # 非推奨: claude プロバイダのみ使用
    # Claude Code CLI のパス（None の場合は PATH から自動検出）
    claude_code_cli_path: str = Field(default="")
    claude_code_timeout_seconds: int = Field(default=120)

    # --- Context-Hub ---
    context_hub_base_url: str = Field(default="http://localhost:8000/api/v1")
    context_hub_api_key: str = Field(default="")
    context_hub_use_mock: bool = Field(default=True)

    # --- Slack ---
    slack_bot_token: str = Field(default="")
    slack_notification_channel: str = Field(default="#ai-pm-alerts")

    # --- 通知チャンネル選択 ---
    # 選択肢: slack | google_sheets | local_file | in_memory
    notification_channel: str = Field(default="slack")

    # --- ローカルファイル通知 ---
    notification_local_dir: str = Field(default="./.ai-pm/notifications")

    # --- Google Sheets 通知 ---
    google_service_account_json: str = Field(default="")
    google_sheet_id: str = Field(default="")

    # --- スケジューラ ---
    # 既定値はこちらで設定。GUI（/settings）でユーザーが調整可能。
    # 実行順序（standup→report_generate→report_deliver→report_reminder→wrap_up→alert_scan）は
    # 時刻設定に関わらず常に固定（schedule_plan.py の CANONICAL_STEP_ORDER）。
    # final_analysis はリーダー確認ゲート駆動のため cron には載せない。
    scheduler_enabled: bool = Field(default=True)
    scheduler_timezone: str = Field(default="Asia/Tokyo")
    # 各フェーズの時刻（時・分）。
    standup_hour: int = Field(default=9)
    standup_minute: int = Field(default=0)
    report_hour: int = Field(default=14)
    report_minute: int = Field(default=0)
    reminder_hour: int = Field(default=17)
    reminder_minute: int = Field(default=0)
    wrap_up_hour: int = Field(default=17)
    wrap_up_minute: int = Field(default=30)
    alert_scan_interval_minutes: int = Field(default=30)

    # --- セキュリティ ---
    cors_origins: str = Field(default="http://localhost:3000")
    jwt_secret: str = Field(default="dev-jwt-secret-change-in-production")
    jwt_expiry_hours: int = Field(default=8)

    # --- 監査ログ ---
    audit_log_retention_days: int = Field(default=365)
    # ローカルファイル出力ディレクトリ。空ならインメモリ（テスト・dev 用）。
    audit_log_dir: str = Field(default="")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """アプリケーション設定のシングルトン。テスト時は lru_cache を clear する。"""
    return Settings()

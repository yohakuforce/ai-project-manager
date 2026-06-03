"""Notifier ファクトリ。settings に応じて適切な実装を返す。

チャンネル選択ロジック:
  1. ``settings.notification_channel`` で指定されたチャンネルを試みる。
  2. 必要な設定が不足している場合は警告を出しフォールバックする。
     フォールバック順: 指定チャンネル → local_file → in_memory
  3. 後方互換: ``notification_channel`` が ``"slack"`` かつ ``slack_bot_token`` が空の場合は
     既存の動作通り InMemoryNotifier を返す（既存テストを壊さない）。
"""

from __future__ import annotations

import logging

from src.config.settings import Settings
from src.infrastructure.notifiers.google_sheets import GoogleSheetsNotifier
from src.infrastructure.notifiers.in_memory import InMemoryNotifier
from src.infrastructure.notifiers.local_file import LocalFileNotifier
from src.infrastructure.notifiers.protocol import Notifier
from src.infrastructure.notifiers.slack_bot import SlackNotifier

logger = logging.getLogger(__name__)


def _build_slack(settings: Settings) -> Notifier | None:
    """SlackNotifier を構築する。設定不足なら None を返す。"""
    if not settings.slack_bot_token:
        logger.warning("SLACK_BOT_TOKEN が未設定のため Slack チャンネルを使用できません。")
        return None
    return SlackNotifier(settings=settings)


def _build_google_sheets(settings: Settings) -> Notifier | None:
    """GoogleSheetsNotifier を構築する。設定不足なら None を返す。"""
    if not settings.google_service_account_json or not settings.google_sheet_id:
        logger.warning(
            "GOOGLE_SERVICE_ACCOUNT_JSON または GOOGLE_SHEET_ID が未設定のため "
            "google_sheets チャンネルを使用できません。"
        )
        return None
    return GoogleSheetsNotifier(settings=settings)


def _build_local_file(settings: Settings) -> Notifier:
    """LocalFileNotifier を構築する。常に成功する。"""
    return LocalFileNotifier(settings=settings)


def _build_in_memory() -> Notifier:
    """InMemoryNotifier を構築する。"""
    return InMemoryNotifier()


def build_notifier(settings: Settings) -> Notifier:
    """settings から Notifier を構築する。

    チャンネル選択:
      - ``notification_channel`` が ``"slack"`` → SlackNotifier（後方互換: token 空なら InMemory）
      - ``notification_channel`` が ``"google_sheets"`` → GoogleSheetsNotifier
        （設定不足 → local_file → in_memory）
      - ``notification_channel`` が ``"local_file"`` → LocalFileNotifier
      - ``notification_channel`` が ``"in_memory"`` → InMemoryNotifier
      - その他の値 → 警告を出し local_file にフォールバック
    """
    channel = settings.notification_channel

    if channel == "slack":
        notifier = _build_slack(settings)
        if notifier is not None:
            return notifier
        # 後方互換: slack_bot_token なし → InMemoryNotifier
        logger.warning(
            "SLACK_BOT_TOKEN が未設定のため InMemoryNotifier を使用します。"
            "production では必ず SLACK_BOT_TOKEN を設定してください。"
        )
        return _build_in_memory()

    if channel == "google_sheets":
        notifier = _build_google_sheets(settings)
        if notifier is not None:
            return notifier
        logger.warning("google_sheets の設定不足のため local_file にフォールバックします。")
        return _build_local_file(settings)

    if channel == "local_file":
        return _build_local_file(settings)

    if channel == "in_memory":
        return _build_in_memory()

    logger.warning(
        "不明な notification_channel '%s' です。local_file にフォールバックします。",
        channel,
    )
    return _build_local_file(settings)

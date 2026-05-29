"""Slack 配信 Notifier 実装。

slack-sdk の ``AsyncWebClient`` を使い、日報テンプレートとアラートを
Slack チャンネル / DM へ配信する。

設定:
  - ``settings.slack_bot_token``: Bot OAuth Token（xoxb-...）
  - ``settings.slack_notification_channel``: アラート既定配信チャンネル

リトライ:
  - 一過性エラー（rate_limited / timeout）は最大 3 回まで指数バックオフでリトライ。
  - それ以外（auth_error / channel_not_found 等）は即時失敗として返す。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from src.config.settings import Settings
from src.domain.alert.aggregate import AlertSeverity
from src.infrastructure.notifiers.protocol import (
    AlertNotification,
    DailyReportNotification,
    NotificationError,
    NotificationResult,
)

logger = logging.getLogger(__name__)

# リトライ対象の Slack エラーコード（一過性）
_RETRYABLE_ERRORS = frozenset({"rate_limited", "timeout", "service_unavailable", "internal_error"})
_MAX_RETRIES = 3
_BASE_BACKOFF_SECONDS = 1.0


@dataclass
class SlackNotifier:
    """Slack 配信 Notifier。

    ``client`` を外部から注入できるためテストでモック可能。
    省略時は ``settings.slack_bot_token`` から AsyncWebClient を構築する。
    """

    settings: Settings
    client: AsyncWebClient = field(init=False)
    _sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)

    def __post_init__(self) -> None:
        if not self.settings.slack_bot_token:
            raise NotificationError("SLACK_BOT_TOKEN が未設定です。.env に設定してください。")
        self.client = AsyncWebClient(token=self.settings.slack_bot_token)

    async def send_daily_report_invite(
        self, notification: DailyReportNotification
    ) -> NotificationResult:
        text, blocks = self._build_daily_report_message(notification)
        return await self._post_with_retry(
            channel=notification.member_channel,
            text=text,
            blocks=blocks,
        )

    async def send_alert(self, notification: AlertNotification) -> NotificationResult:
        text, blocks = self._build_alert_message(notification)
        return await self._post_with_retry(
            channel=notification.recipient_channel,
            text=text,
            blocks=blocks,
        )

    async def healthcheck(self) -> bool:
        try:
            response = await self.client.auth_test()
            return bool(response.get("ok", False))
        except SlackApiError as exc:
            logger.warning("Slack healthcheck 失敗: %s", exc.response.get("error"))
            return False
        except Exception as exc:
            logger.warning("Slack healthcheck 例外: %s", exc)
            return False

    # ------------------------------------------------------------------
    # 内部: 配信ロジック
    # ------------------------------------------------------------------

    async def _post_with_retry(
        self,
        *,
        channel: str,
        text: str,
        blocks: list[dict[str, Any]],
    ) -> NotificationResult:
        """指数バックオフ付きでメッセージを投稿する。"""
        last_error: str | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await self.client.chat_postMessage(
                    channel=channel,
                    text=text,
                    blocks=blocks,
                )
                if response.get("ok"):
                    return NotificationResult(
                        success=True,
                        channel=channel,
                        message_id=str(response.get("ts", "")),
                    )
                last_error = str(response.get("error", "unknown"))
                if last_error not in _RETRYABLE_ERRORS:
                    break
            except SlackApiError as exc:
                last_error = str(exc.response.get("error", str(exc)))
                if last_error not in _RETRYABLE_ERRORS:
                    logger.error(
                        "Slack 配信失敗（非リトライ）: channel=%s error=%s",
                        channel,
                        last_error,
                    )
                    break
            except Exception as exc:
                last_error = f"unexpected: {exc!r}"
                logger.exception("Slack 配信中に予期せぬ例外")
                break

            if attempt < _MAX_RETRIES:
                backoff = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Slack 配信リトライ %d/%d: channel=%s error=%s backoff=%.1fs",
                    attempt,
                    _MAX_RETRIES,
                    channel,
                    last_error,
                    backoff,
                )
                await self._sleep(backoff)

        return NotificationResult(
            success=False,
            channel=channel,
            error=last_error or "unknown_error",
        )

    # ------------------------------------------------------------------
    # 内部: メッセージビルダー
    # ------------------------------------------------------------------

    @staticmethod
    def _build_daily_report_message(
        notification: DailyReportNotification,
    ) -> tuple[str, list[dict[str, Any]]]:
        report = notification.report
        member_name = notification.member_name
        report_date = report.report_date.isoformat()
        question_count = len(report.template.questions)

        text = (
            f"📋 {member_name} さん、本日（{report_date}）の日報です。"
            f"{question_count} 件の質問にお答えください。"
        )

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"日報 — {report_date}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{member_name}* さん、本日の日報をお願いします。\n"
                        f"質問数: *{question_count}* 件"
                    ),
                },
            },
            {"type": "divider"},
        ]

        for idx, question in enumerate(report.template.questions, start=1):
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Q{idx}.* {question.body}",
                    },
                }
            )

        if notification.submit_url:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "回答する"},
                            "url": notification.submit_url,
                            "style": "primary",
                        }
                    ],
                }
            )

        return text, blocks

    @staticmethod
    def _build_alert_message(
        notification: AlertNotification,
    ) -> tuple[str, list[dict[str, Any]]]:
        alert = notification.alert
        emoji = _SEVERITY_EMOJI.get(alert.severity, "⚠️")
        project = notification.project_name
        target = notification.target_member_name or "—"

        text = f"{emoji} [{alert.severity.value.upper()}] {project}: {alert.ai_generated_message}"

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} アラート: {alert.category.value}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*プロジェクト*\n{project}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*重要度*\n{alert.severity.value}",
                    },
                    {"type": "mrkdwn", "text": f"*対象メンバー*\n{target}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*検出時刻*\n{alert.detected_at.isoformat()}",
                    },
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*内容*\n{alert.ai_generated_message}",
                },
            },
        ]

        if alert.evidence:
            evidence_lines = "\n".join(f"• {e.human_readable_summary}" for e in alert.evidence)
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*根拠*\n{evidence_lines}",
                    },
                }
            )

        return text, blocks


_SEVERITY_EMOJI: dict[AlertSeverity, str] = {
    AlertSeverity.CRITICAL: "🚨",
    AlertSeverity.HIGH: "⚠️",
    AlertSeverity.MEDIUM: "ℹ️",
}

"""Notifier 層のユニットテスト。

カバー範囲:
  - InMemoryNotifier: 配信成功 / 失敗 / フィルタ / リセット
  - SlackNotifier: メッセージビルダー / 配信成功 / リトライ / 非リトライエラー / healthcheck
  - factory: settings に応じた適切な実装の選択
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from slack_sdk.errors import SlackApiError

from src.config.settings import Settings
from src.domain.alert.aggregate import (
    Alert,
    AlertCategory,
    AlertId,
    AlertSeverity,
    Evidence,
    EvidenceType,
)
from src.domain.reporting.aggregate import DailyReport
from src.domain.reporting.value_objects import (
    DailyReportId,
    QuestionId,
    QuestionType,
    ReportQuestion,
    ReportTemplate,
)
from src.infrastructure.notifiers import (
    AlertNotification,
    DailyReportNotification,
    InMemoryNotifier,
    NotificationError,
    SlackNotifier,
    build_notifier,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_daily_report() -> DailyReport:
    """テスト用 DailyReport を組み立てる。"""
    questions = [
        ReportQuestion(
            question_id=QuestionId.generate(),
            question_type=QuestionType.PROGRESS_PERCENT,
            body="「機能A」の進捗率（0〜100）を入力してください。",
            task_id="task-1",
        ),
        ReportQuestion(
            question_id=QuestionId.generate(),
            question_type=QuestionType.BLOCKER,
            body="ブロッカーはありますか？",
            task_id="task-1",
        ),
    ]
    return DailyReport(
        report_id=DailyReportId.generate(),
        member_id="member-1",
        project_id="project-1",
        report_date=date(2026, 5, 17),
        template=ReportTemplate.create(questions),
    )


def _make_alert(*, severity: AlertSeverity = AlertSeverity.HIGH) -> Alert:
    return Alert(
        alert_id=AlertId.generate(),
        project_id="project-1",
        category=AlertCategory.TASK_DELAY,
        severity=severity,
        ai_generated_message="タスク X が予定日を 2 日超過しています。",
        evidence=[
            Evidence(
                evidence_type=EvidenceType.TASK_STATUS,
                data_ref="task-x",
                human_readable_summary="due=2026-05-15 / status=IN_PROGRESS",
            )
        ],
        target_member_id="member-1",
        detected_at=datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
    )


def _make_report_notification() -> DailyReportNotification:
    return DailyReportNotification(
        report=_make_daily_report(),
        member_name="山田 太郎",
        member_channel="@yamada",
        submit_url="https://example.com/reports/1",
    )


def _make_alert_notification() -> AlertNotification:
    return AlertNotification(
        alert=_make_alert(),
        project_name="案件 X",
        recipient_channel="#ai-pm-alerts",
        target_member_name="山田 太郎",
    )


@pytest.fixture
def settings_with_token() -> Settings:
    return Settings(slack_bot_token="xoxb-test-token-123")


# ---------------------------------------------------------------------------
# InMemoryNotifier
# ---------------------------------------------------------------------------


class TestInMemoryNotifier:
    async def test_send_daily_report_invite_success(self) -> None:
        notifier = InMemoryNotifier()
        result = await notifier.send_daily_report_invite(_make_report_notification())

        assert result.success is True
        assert result.channel == "@yamada"
        assert result.message_id is not None
        assert result.message_id.startswith("mem-")
        assert len(notifier.sent) == 1
        assert notifier.sent[0].kind == "daily_report"

    async def test_send_alert_success(self) -> None:
        notifier = InMemoryNotifier()
        result = await notifier.send_alert(_make_alert_notification())

        assert result.success is True
        assert result.channel == "#ai-pm-alerts"
        assert len(notifier.filter("alert")) == 1

    async def test_send_fails_when_configured(self) -> None:
        notifier = InMemoryNotifier(fail_on_send=True)
        result = await notifier.send_alert(_make_alert_notification())

        assert result.success is False
        assert result.error == "InMemoryNotifier configured to fail"
        assert notifier.sent == []

    async def test_healthcheck_reflects_fail_flag(self) -> None:
        ok = InMemoryNotifier()
        ng = InMemoryNotifier(fail_on_send=True)
        assert await ok.healthcheck() is True
        assert await ng.healthcheck() is False

    async def test_reset_clears_sent_buffer(self) -> None:
        notifier = InMemoryNotifier()
        await notifier.send_alert(_make_alert_notification())
        assert len(notifier.sent) == 1
        notifier.reset()
        assert notifier.sent == []

    async def test_filter_returns_only_matching_kind(self) -> None:
        notifier = InMemoryNotifier()
        await notifier.send_daily_report_invite(_make_report_notification())
        await notifier.send_alert(_make_alert_notification())
        await notifier.send_alert(_make_alert_notification())

        assert len(notifier.filter("daily_report")) == 1
        assert len(notifier.filter("alert")) == 2


# ---------------------------------------------------------------------------
# SlackNotifier — message builders
# ---------------------------------------------------------------------------


class TestSlackNotifierMessageBuilders:
    def test_build_daily_report_contains_member_and_questions(
        self, settings_with_token: Settings
    ) -> None:
        notifier = SlackNotifier(settings=settings_with_token)
        notification = _make_report_notification()

        text, blocks = notifier._build_daily_report_message(notification)

        assert "山田 太郎" in text
        assert "2026-05-17" in text
        # ヘッダ + セクション + ディバイダ + 質問 2 件 + button
        question_blocks = [b for b in blocks if b["type"] == "section" and "Q" in b["text"]["text"]]
        assert len(question_blocks) == 2
        assert any(b["type"] == "actions" for b in blocks)

    def test_build_daily_report_skips_button_without_url(
        self, settings_with_token: Settings
    ) -> None:
        notifier = SlackNotifier(settings=settings_with_token)
        notification = DailyReportNotification(
            report=_make_daily_report(),
            member_name="山田 太郎",
            member_channel="@yamada",
            submit_url=None,
        )

        _, blocks = notifier._build_daily_report_message(notification)
        assert not any(b["type"] == "actions" for b in blocks)

    @pytest.mark.parametrize(
        ("severity", "expected_emoji"),
        [
            (AlertSeverity.CRITICAL, "🚨"),
            (AlertSeverity.HIGH, "⚠️"),
            (AlertSeverity.MEDIUM, "ℹ️"),
        ],
    )
    def test_build_alert_uses_severity_emoji(
        self,
        settings_with_token: Settings,
        severity: AlertSeverity,
        expected_emoji: str,
    ) -> None:
        notifier = SlackNotifier(settings=settings_with_token)
        notification = AlertNotification(
            alert=_make_alert(severity=severity),
            project_name="案件 X",
            recipient_channel="#ai-pm-alerts",
            target_member_name="山田 太郎",
        )

        text, blocks = notifier._build_alert_message(notification)

        assert expected_emoji in text
        evidence_block = next(
            b for b in blocks if b.get("text", {}).get("text", "").startswith("*根拠*")
        )
        assert "due=2026-05-15" in evidence_block["text"]["text"]


# ---------------------------------------------------------------------------
# SlackNotifier — sending behavior
# ---------------------------------------------------------------------------


def _ok_response(ts: str = "1700000000.000100") -> dict[str, Any]:
    return {"ok": True, "ts": ts}


def _err_response(error: str) -> SlackApiError:
    """SlackApiError を ``response`` が dict-like な状態で組み立てる。"""
    return SlackApiError(
        message=error,
        response={"ok": False, "error": error},  # type: ignore[arg-type]
    )


class TestSlackNotifierSending:
    async def test_send_daily_report_succeeds(self, settings_with_token: Settings) -> None:
        notifier = SlackNotifier(settings=settings_with_token)
        notifier.client.chat_postMessage = AsyncMock(  # type: ignore[method-assign]
            return_value=_ok_response("1.001")
        )

        result = await notifier.send_daily_report_invite(_make_report_notification())

        assert result.success is True
        assert result.message_id == "1.001"
        notifier.client.chat_postMessage.assert_awaited_once()

    async def test_send_alert_retries_on_rate_limited(self, settings_with_token: Settings) -> None:
        notifier = SlackNotifier(settings=settings_with_token)
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        notifier._sleep = fake_sleep  # type: ignore[assignment]
        notifier.client.chat_postMessage = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                _err_response("rate_limited"),
                _err_response("rate_limited"),
                _ok_response("1.500"),
            ]
        )

        result = await notifier.send_alert(_make_alert_notification())

        assert result.success is True
        assert result.message_id == "1.500"
        assert notifier.client.chat_postMessage.await_count == 3
        # 指数バックオフ: 1.0, 2.0
        assert sleep_calls == [1.0, 2.0]

    async def test_send_alert_fails_fast_on_non_retryable(
        self, settings_with_token: Settings
    ) -> None:
        notifier = SlackNotifier(settings=settings_with_token)
        notifier._sleep = AsyncMock()  # type: ignore[assignment]
        notifier.client.chat_postMessage = AsyncMock(  # type: ignore[method-assign]
            side_effect=_err_response("channel_not_found")
        )

        result = await notifier.send_alert(_make_alert_notification())

        assert result.success is False
        assert result.error == "channel_not_found"
        # 非リトライエラーなので 1 回のみ
        assert notifier.client.chat_postMessage.await_count == 1

    async def test_send_alert_exhausts_retries(self, settings_with_token: Settings) -> None:
        notifier = SlackNotifier(settings=settings_with_token)
        notifier._sleep = AsyncMock()  # type: ignore[assignment]
        notifier.client.chat_postMessage = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                _err_response("rate_limited"),
                _err_response("rate_limited"),
                _err_response("rate_limited"),
            ]
        )

        result = await notifier.send_alert(_make_alert_notification())

        assert result.success is False
        assert result.error == "rate_limited"
        assert notifier.client.chat_postMessage.await_count == 3

    async def test_constructor_rejects_empty_token(self) -> None:
        settings = Settings(slack_bot_token="")
        with pytest.raises(NotificationError):
            SlackNotifier(settings=settings)

    async def test_healthcheck_ok(self, settings_with_token: Settings) -> None:
        notifier = SlackNotifier(settings=settings_with_token)
        notifier.client.auth_test = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]
        assert await notifier.healthcheck() is True

    async def test_healthcheck_handles_slack_api_error(self, settings_with_token: Settings) -> None:
        notifier = SlackNotifier(settings=settings_with_token)
        notifier.client.auth_test = AsyncMock(  # type: ignore[method-assign]
            side_effect=_err_response("invalid_auth")
        )
        assert await notifier.healthcheck() is False


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------


class TestBuildNotifier:
    def test_returns_in_memory_when_token_missing(self) -> None:
        settings = Settings(slack_bot_token="")
        notifier = build_notifier(settings)
        assert isinstance(notifier, InMemoryNotifier)

    def test_returns_slack_when_token_present(self) -> None:
        settings = Settings(slack_bot_token="xoxb-test")
        notifier = build_notifier(settings)
        assert isinstance(notifier, SlackNotifier)


# ---------------------------------------------------------------------------
# Sanity: asyncio loop integration
# ---------------------------------------------------------------------------


def test_event_loop_runs_in_memory_notifier() -> None:
    """asyncio_mode=auto に依存せずループで動くことを確認。"""

    async def scenario() -> None:
        notifier = InMemoryNotifier()
        await notifier.send_alert(_make_alert_notification())
        assert len(notifier.sent) == 1

    asyncio.run(scenario())

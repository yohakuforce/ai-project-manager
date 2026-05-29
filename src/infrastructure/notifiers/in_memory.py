"""テスト・開発用の InMemoryNotifier。

外部依存なしで動作し、配信されたメッセージを ``sent`` リストに保持する。
ユニットテスト・統合テスト・Mock LLM での E2E 動作確認に使用する。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from src.infrastructure.notifiers.protocol import (
    AlertNotification,
    DailyReportNotification,
    NotificationResult,
)


@dataclass
class _Sent:
    kind: str  # "daily_report" | "alert"
    channel: str
    payload: DailyReportNotification | AlertNotification


@dataclass
class InMemoryNotifier:
    """配信されたメッセージをメモリに保持するテスト用 Notifier。

    Notifier プロトコルを満たす。production では使わない。
    """

    fail_on_send: bool = False
    sent: list[_Sent] = field(default_factory=list)

    async def send_daily_report_invite(
        self, notification: DailyReportNotification
    ) -> NotificationResult:
        if self.fail_on_send:
            return NotificationResult(
                success=False,
                channel=notification.member_channel,
                error="InMemoryNotifier configured to fail",
            )
        self.sent.append(
            _Sent(
                kind="daily_report",
                channel=notification.member_channel,
                payload=notification,
            )
        )
        return NotificationResult(
            success=True,
            channel=notification.member_channel,
            message_id=f"mem-{uuid.uuid4()}",
        )

    async def send_alert(self, notification: AlertNotification) -> NotificationResult:
        if self.fail_on_send:
            return NotificationResult(
                success=False,
                channel=notification.recipient_channel,
                error="InMemoryNotifier configured to fail",
            )
        self.sent.append(
            _Sent(
                kind="alert",
                channel=notification.recipient_channel,
                payload=notification,
            )
        )
        return NotificationResult(
            success=True,
            channel=notification.recipient_channel,
            message_id=f"mem-{uuid.uuid4()}",
        )

    async def healthcheck(self) -> bool:
        return not self.fail_on_send

    def reset(self) -> None:
        self.sent.clear()

    def filter(self, kind: str) -> list[_Sent]:
        return [s for s in self.sent if s.kind == kind]

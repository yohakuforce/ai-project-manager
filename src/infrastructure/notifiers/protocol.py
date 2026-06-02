"""Notifier プロトコル定義。

ドメイン層・Application 層はこのプロトコルにのみ依存する。
具体的な配信実装（Slack / Email / Mock）は本パッケージ内のアダプタで提供。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from src.domain.alert.aggregate import Alert
from src.domain.reporting.aggregate import DailyReport


class NotificationError(Exception):
    """通知配信失敗を表す例外。配信先 API エラー等を内包する。"""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


@dataclass(frozen=True)
class NotificationResult:
    """通知配信結果。

    成功時は ``message_id`` に配信先固有の ID（Slack の ts 等）が入る。
    失敗時は ``error`` にエラーメッセージが入る（例外は呼び出し側で潰される設計）。
    """

    success: bool
    channel: str
    message_id: str | None = None
    error: str | None = None
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class DailyReportNotification:
    """日報配信通知のペイロード。

    Notifier 実装はこのペイロードから配信先固有のメッセージを構築する。
    """

    report: DailyReport
    member_name: str
    member_channel: str  # 例: Slack DM の channel id、メールアドレス等
    submit_url: str | None = None  # 回答フォームへのリンク（あれば）


@dataclass(frozen=True)
class AlertNotification:
    """アラート通知のペイロード。"""

    alert: Alert
    project_name: str
    recipient_channel: str  # 例: '#ai-pm-alerts'
    target_member_name: str | None = None


@dataclass(frozen=True)
class MessageNotification:
    """汎用メッセージ通知のペイロード。

    日報以外のスタンドアップ・催促・総括・全体ステータス・リーダー確認ゲート等、
    定型化されていない文面を任意のチャネル（リーダーチャネル or メンバー DM）へ
    配信するために使う。

    kind は配信先での仕分け・テスト検証用のタグ（例: 'standup' / 'reminder' /
    'wrap_up' / 'status' / 'gate'）。action_url はリーダーが確認操作を行う
    エンドポイント等への導線（あれば）。
    """

    channel: str
    title: str
    body: str
    kind: str = "message"
    action_url: str | None = None


@runtime_checkable
class Notifier(Protocol):
    """通知配信プロトコル。

    実装上の不変条件:
      - 配信失敗時は ``NotificationResult(success=False, error=...)`` を返す
        か ``NotificationError`` を送出する。実装ごとに統一されていればよい。
      - 通知ペイロードを不変オブジェクトとして受け取り、副作用は外部 API 呼び出しのみ。
      - 冪等性は実装側で保証しない（呼び出し側で重複防止）。
    """

    async def send_daily_report_invite(
        self, notification: DailyReportNotification
    ) -> NotificationResult:
        """日報テンプレートを対象メンバーへ配信する。"""
        ...

    async def send_alert(self, notification: AlertNotification) -> NotificationResult:
        """アラートを PL/PM チャンネルへ通知する。"""
        ...

    async def send_message(self, notification: MessageNotification) -> NotificationResult:
        """汎用メッセージ（スタンドアップ・催促・総括・ゲート等）を配信する。"""
        ...

    async def healthcheck(self) -> bool:
        """配信先の到達性確認。CI / 起動時に呼ぶ。"""
        ...

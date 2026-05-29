"""通知配信層。

責務:
  - 日報テンプレートをメンバーへ配信
  - アラートを PL/PM へ通知
  - 配信先（Slack / Google Sheets / ローカルファイル / Mock）を抽象化

外部依存（slack-sdk / gspread 等）は本パッケージ配下のアダプタに閉じる。
ドメイン層・Application 層は Notifier プロトコルにのみ依存する。
"""

from src.infrastructure.notifiers.factory import build_notifier
from src.infrastructure.notifiers.google_sheets import GoogleSheetsNotifier
from src.infrastructure.notifiers.in_memory import InMemoryNotifier
from src.infrastructure.notifiers.local_file import LocalFileNotifier
from src.infrastructure.notifiers.protocol import (
    AlertNotification,
    DailyReportNotification,
    NotificationError,
    NotificationResult,
    Notifier,
)
from src.infrastructure.notifiers.slack_bot import SlackNotifier

__all__ = [
    "AlertNotification",
    "DailyReportNotification",
    "GoogleSheetsNotifier",
    "InMemoryNotifier",
    "LocalFileNotifier",
    "NotificationError",
    "NotificationResult",
    "Notifier",
    "SlackNotifier",
    "build_notifier",
]

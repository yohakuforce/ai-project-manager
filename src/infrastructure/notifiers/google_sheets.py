"""Google Sheets 配信 Notifier 実装。

gspread を使い、通知ごとにスプレッドシートへ 1 行追記する。

設定:
  - ``settings.google_service_account_json``: サービスアカウント JSON ファイルのパス
  - ``settings.google_sheet_id``            : 書き込み先スプレッドシートの ID

シート構成（自動作成）:
  - シート名 ``daily_reports``: 日報配信通知を追記
  - シート名 ``alerts``       : アラート通知を追記

注意:
  - gspread はメソッド内で遅延インポートするため、インポート時点では
    gspread が未インストールでもエラーにならない。
  - 認証情報ファイルが存在しない・gspread がない場合は NotificationResult(success=False) を返す。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.config.settings import Settings
from src.infrastructure.notifiers.protocol import (
    AlertNotification,
    DailyReportNotification,
    MessageNotification,
    NotificationError,
    NotificationResult,
)

logger = logging.getLogger(__name__)

_DAILY_REPORT_SHEET = "daily_reports"
_ALERT_SHEET = "alerts"
_MESSAGE_SHEET = "messages"

_DAILY_REPORT_HEADERS = [
    "sent_at",
    "member_name",
    "member_channel",
    "report_date",
    "project_id",
    "question_count",
    "submit_url",
]

_ALERT_HEADERS = [
    "sent_at",
    "alert_id",
    "project_name",
    "recipient_channel",
    "target_member_name",
    "severity",
    "category",
    "message",
    "detected_at",
]

_MESSAGE_HEADERS = [
    "sent_at",
    "kind",
    "channel",
    "title",
    "body",
    "action_url",
]


def _import_gspread() -> Any:
    """gspread を遅延インポートする。未インストールの場合は ImportError を送出する。"""
    try:
        import gspread

        return gspread
    except ImportError as exc:
        raise NotificationError(
            "gspread がインストールされていません。`pip install gspread` を実行してください。",
            cause=exc,
        ) from exc


@dataclass
class GoogleSheetsNotifier:
    """Google Sheets へ通知を追記する Notifier。

    ``gspread.Client`` を外部から注入できるためテストでモック可能。
    省略時は ``settings.google_service_account_json`` からサービスアカウント認証を行う。
    """

    settings: Settings
    _client: object | None = None  # gspread.Client (型アノテーションは遅延のため object)

    def _get_client(self) -> Any:
        """gspread クライアントを取得する。未初期化なら認証して返す。"""
        if self._client is not None:
            return self._client

        gspread = _import_gspread()

        creds_path = self.settings.google_service_account_json
        if not creds_path:
            raise NotificationError(
                "GOOGLE_SERVICE_ACCOUNT_JSON が未設定です。.env に設定してください。"
            )

        import os

        if not os.path.exists(creds_path):
            raise NotificationError(f"サービスアカウント JSON が見つかりません: {creds_path}")

        try:
            client = gspread.service_account(filename=creds_path)
            self._client = client
            return client
        except Exception as exc:
            raise NotificationError(
                f"Google サービスアカウント認証に失敗しました: {exc}", cause=exc
            ) from exc

    def _get_or_create_sheet(self, client: Any, sheet_name: str, headers: list[str]) -> Any:
        """スプレッドシートからシートを取得し、存在しなければ作成してヘッダ行を追加する。"""
        if not self.settings.google_sheet_id:
            raise NotificationError("GOOGLE_SHEET_ID が未設定です。.env に設定してください。")
        try:
            spreadsheet = client.open_by_key(self.settings.google_sheet_id)
        except Exception as exc:
            raise NotificationError(
                f"スプレッドシートのオープンに失敗しました (ID={self.settings.google_sheet_id}): {exc}",
                cause=exc,
            ) from exc

        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except Exception:
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=len(headers))
            worksheet.append_row(headers)

        return worksheet

    async def send_daily_report_invite(
        self, notification: DailyReportNotification
    ) -> NotificationResult:
        """日報通知を Google Sheets に追記する。"""
        try:
            client = self._get_client()
            worksheet = self._get_or_create_sheet(
                client, _DAILY_REPORT_SHEET, _DAILY_REPORT_HEADERS
            )
            row = [
                datetime.now(UTC).isoformat(),
                notification.member_name,
                notification.member_channel,
                notification.report.report_date.isoformat(),
                notification.report.project_id,
                len(notification.report.template.questions),
                notification.submit_url or "",
            ]
            result = worksheet.append_row(row)
            row_ref = str(result.get("updates", {}).get("updatedRange", "unknown"))
            logger.info(
                "GoogleSheetsNotifier: 日報通知を追記しました channel=%s range=%s",
                notification.member_channel,
                row_ref,
            )
            return NotificationResult(
                success=True,
                channel="google_sheets",
                message_id=row_ref,
            )
        except NotificationError as exc:
            logger.error("GoogleSheetsNotifier: 日報通知の書き込みに失敗: %s", exc)
            return NotificationResult(
                success=False,
                channel="google_sheets",
                error=str(exc),
            )
        except Exception as exc:
            logger.error("GoogleSheetsNotifier: 予期せぬエラー: %s", exc)
            return NotificationResult(
                success=False,
                channel="google_sheets",
                error=f"unexpected: {exc!r}",
            )

    async def send_alert(self, notification: AlertNotification) -> NotificationResult:
        """アラート通知を Google Sheets に追記する。"""
        alert = notification.alert
        try:
            client = self._get_client()
            worksheet = self._get_or_create_sheet(client, _ALERT_SHEET, _ALERT_HEADERS)
            row = [
                datetime.now(UTC).isoformat(),
                str(alert.alert_id),
                notification.project_name,
                notification.recipient_channel,
                notification.target_member_name or "",
                alert.severity.value,
                alert.category.value,
                alert.ai_generated_message,
                alert.detected_at.isoformat(),
            ]
            result = worksheet.append_row(row)
            row_ref = str(result.get("updates", {}).get("updatedRange", "unknown"))
            logger.info(
                "GoogleSheetsNotifier: アラート通知を追記しました channel=%s range=%s",
                notification.recipient_channel,
                row_ref,
            )
            return NotificationResult(
                success=True,
                channel="google_sheets",
                message_id=row_ref,
            )
        except NotificationError as exc:
            logger.error("GoogleSheetsNotifier: アラート通知の書き込みに失敗: %s", exc)
            return NotificationResult(
                success=False,
                channel="google_sheets",
                error=str(exc),
            )
        except Exception as exc:
            logger.error("GoogleSheetsNotifier: 予期せぬエラー: %s", exc)
            return NotificationResult(
                success=False,
                channel="google_sheets",
                error=f"unexpected: {exc!r}",
            )

    async def send_message(self, notification: MessageNotification) -> NotificationResult:
        """汎用メッセージ通知を Google Sheets に追記する。"""
        try:
            client = self._get_client()
            worksheet = self._get_or_create_sheet(client, _MESSAGE_SHEET, _MESSAGE_HEADERS)
            row = [
                datetime.now(UTC).isoformat(),
                notification.kind,
                notification.channel,
                notification.title,
                notification.body,
                notification.action_url or "",
            ]
            result = worksheet.append_row(row)
            row_ref = str(result.get("updates", {}).get("updatedRange", "unknown"))
            logger.info(
                "GoogleSheetsNotifier: メッセージ通知を追記しました kind=%s channel=%s range=%s",
                notification.kind,
                notification.channel,
                row_ref,
            )
            return NotificationResult(
                success=True,
                channel="google_sheets",
                message_id=row_ref,
            )
        except NotificationError as exc:
            logger.error("GoogleSheetsNotifier: メッセージ通知の書き込みに失敗: %s", exc)
            return NotificationResult(
                success=False,
                channel="google_sheets",
                error=str(exc),
            )
        except Exception as exc:
            logger.error("GoogleSheetsNotifier: 予期せぬエラー: %s", exc)
            return NotificationResult(
                success=False,
                channel="google_sheets",
                error=f"unexpected: {exc!r}",
            )

    async def healthcheck(self) -> bool:
        """Google Sheets への接続確認。スプレッドシートをオープンできれば True を返す。"""
        try:
            client = self._get_client()
            if not self.settings.google_sheet_id:
                return False
            client.open_by_key(self.settings.google_sheet_id)
            return True
        except Exception as exc:
            logger.warning("GoogleSheetsNotifier healthcheck 失敗: %s", exc)
            return False

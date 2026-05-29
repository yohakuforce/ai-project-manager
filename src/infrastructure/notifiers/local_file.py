"""ローカルファイル配信 Notifier 実装。

各通知を JSONL 形式でローカルディレクトリに追記する。
Slack トークンや Google サービスアカウントが使えない環境でのフォールバックとして使用する。

設定:
  - ``settings.notification_local_dir``: 出力先ディレクトリ（デフォルト: ``./.ai-pm/notifications``）

ファイル構成:
  - ``<dir>/daily_reports.jsonl``: 日報配信通知（1 行 = 1 通知）
  - ``<dir>/alerts.jsonl``       : アラート通知（1 行 = 1 通知）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.config.settings import Settings
from src.infrastructure.notifiers.protocol import (
    AlertNotification,
    DailyReportNotification,
    NotificationError,
    NotificationResult,
)

logger = logging.getLogger(__name__)

_DAILY_REPORT_FILE = "daily_reports.jsonl"
_ALERT_FILE = "alerts.jsonl"


@dataclass
class LocalFileNotifier:
    """ローカルファイルへ通知を追記する Notifier。

    各通知は JSONL 形式（1 行 = 1 JSON オブジェクト）でファイルに書き込まれる。
    ディレクトリが存在しない場合は自動で作成する。
    """

    settings: Settings

    @property
    def _base_dir(self) -> Path:
        return Path(self.settings.notification_local_dir)

    def _ensure_dir(self) -> None:
        """出力ディレクトリが存在しない場合は作成する。"""
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _append_line(self, filename: str, payload: dict) -> str:
        """JSONL ファイルに 1 行追記し、ファイルパスを返す。

        IO エラーは ``NotificationError`` に変換して送出する。
        """
        try:
            self._ensure_dir()
            filepath = self._base_dir / filename
            line = json.dumps(payload, ensure_ascii=False, default=str)
            with filepath.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            return str(filepath)
        except OSError as exc:
            raise NotificationError(
                f"ローカルファイルへの書き込みに失敗しました: {exc}", cause=exc
            ) from exc

    async def send_daily_report_invite(
        self, notification: DailyReportNotification
    ) -> NotificationResult:
        """日報通知を JSONL ファイルに追記する。"""
        payload = {
            "type": "daily_report",
            "sent_at": datetime.now(UTC).isoformat(),
            "member_name": notification.member_name,
            "member_channel": notification.member_channel,
            "report_date": notification.report.report_date.isoformat(),
            "project_id": notification.report.project_id,
            "question_count": len(notification.report.template.questions),
            "submit_url": notification.submit_url,
        }
        try:
            filepath = self._append_line(_DAILY_REPORT_FILE, payload)
            logger.info(
                "LocalFileNotifier: 日報通知を書き込みました channel=%s file=%s",
                notification.member_channel,
                filepath,
            )
            return NotificationResult(
                success=True,
                channel="local_file",
                message_id=filepath,
            )
        except NotificationError as exc:
            logger.error("LocalFileNotifier: 日報通知の書き込みに失敗: %s", exc)
            return NotificationResult(
                success=False,
                channel="local_file",
                error=str(exc),
            )

    async def send_alert(self, notification: AlertNotification) -> NotificationResult:
        """アラート通知を JSONL ファイルに追記する。"""
        alert = notification.alert
        payload = {
            "type": "alert",
            "sent_at": datetime.now(UTC).isoformat(),
            "alert_id": str(alert.alert_id),
            "project_name": notification.project_name,
            "recipient_channel": notification.recipient_channel,
            "target_member_name": notification.target_member_name,
            "severity": alert.severity.value,
            "category": alert.category.value,
            "message": alert.ai_generated_message,
            "detected_at": alert.detected_at.isoformat(),
        }
        try:
            filepath = self._append_line(_ALERT_FILE, payload)
            logger.info(
                "LocalFileNotifier: アラート通知を書き込みました channel=%s file=%s",
                notification.recipient_channel,
                filepath,
            )
            return NotificationResult(
                success=True,
                channel="local_file",
                message_id=filepath,
            )
        except NotificationError as exc:
            logger.error("LocalFileNotifier: アラート通知の書き込みに失敗: %s", exc)
            return NotificationResult(
                success=False,
                channel="local_file",
                error=str(exc),
            )

    async def healthcheck(self) -> bool:
        """出力ディレクトリへの書き込み可否を確認する。"""
        try:
            self._ensure_dir()
            test_file = self._base_dir / ".healthcheck"
            test_file.touch()
            test_file.unlink()
            return True
        except OSError as exc:
            logger.warning("LocalFileNotifier healthcheck 失敗: %s", exc)
            return False

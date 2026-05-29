"""マルチチャンネル Notifier のユニットテスト。

カバー範囲:
  - LocalFileNotifier: 配信成功 / IO エラー / healthcheck
  - GoogleSheetsNotifier: 配信成功 / 設定不足 / gspread 未インストール / healthcheck
  - factory: notification_channel に応じた Notifier 選択とフォールバック
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    GoogleSheetsNotifier,
    InMemoryNotifier,
    LocalFileNotifier,
    NotificationError,
    SlackNotifier,
    build_notifier,
)

# ---------------------------------------------------------------------------
# Fixtures / ヘルパー
# ---------------------------------------------------------------------------


def _make_daily_report() -> DailyReport:
    questions = [
        ReportQuestion(
            question_id=QuestionId.generate(),
            question_type=QuestionType.PROGRESS_PERCENT,
            body="「機能A」の進捗率（0〜100）を入力してください。",
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


def _settings_local(tmp_path: Path) -> Settings:
    return Settings(
        notification_channel="local_file",
        notification_local_dir=str(tmp_path / "notifications"),
    )


# ---------------------------------------------------------------------------
# LocalFileNotifier
# ---------------------------------------------------------------------------


class TestLocalFileNotifier:
    async def test_send_daily_report_creates_jsonl(self, tmp_path: Path) -> None:
        notifier = LocalFileNotifier(settings=_settings_local(tmp_path))
        result = await notifier.send_daily_report_invite(_make_report_notification())

        assert result.success is True
        assert result.channel == "local_file"
        assert result.message_id is not None

        output_file = Path(result.message_id)
        assert output_file.exists()
        line = json.loads(output_file.read_text(encoding="utf-8").strip())
        assert line["type"] == "daily_report"
        assert line["member_name"] == "山田 太郎"
        assert line["report_date"] == "2026-05-17"

    async def test_send_alert_creates_jsonl(self, tmp_path: Path) -> None:
        notifier = LocalFileNotifier(settings=_settings_local(tmp_path))
        result = await notifier.send_alert(_make_alert_notification())

        assert result.success is True
        assert result.channel == "local_file"

        output_file = Path(result.message_id)
        line = json.loads(output_file.read_text(encoding="utf-8").strip())
        assert line["type"] == "alert"
        assert line["project_name"] == "案件 X"
        assert line["severity"] == "high"

    async def test_multiple_notifications_appended_as_separate_lines(
        self, tmp_path: Path
    ) -> None:
        notifier = LocalFileNotifier(settings=_settings_local(tmp_path))
        await notifier.send_alert(_make_alert_notification())
        await notifier.send_alert(_make_alert_notification())

        result = await notifier.send_alert(_make_alert_notification())
        output_file = Path(result.message_id)
        lines = [ln for ln in output_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 3

    async def test_send_daily_report_returns_failure_on_io_error(
        self, tmp_path: Path
    ) -> None:
        notifier = LocalFileNotifier(settings=_settings_local(tmp_path))
        # _append_line が OSError を送出するようにパッチ
        with patch.object(
            notifier, "_append_line", side_effect=NotificationError("IO 失敗")
        ):
            result = await notifier.send_daily_report_invite(_make_report_notification())

        assert result.success is False
        assert result.channel == "local_file"
        assert "IO 失敗" in (result.error or "")

    async def test_send_alert_returns_failure_on_io_error(self, tmp_path: Path) -> None:
        notifier = LocalFileNotifier(settings=_settings_local(tmp_path))
        with patch.object(
            notifier, "_append_line", side_effect=NotificationError("IO 失敗")
        ):
            result = await notifier.send_alert(_make_alert_notification())

        assert result.success is False
        assert "IO 失敗" in (result.error or "")

    async def test_healthcheck_returns_true_when_dir_writable(
        self, tmp_path: Path
    ) -> None:
        notifier = LocalFileNotifier(settings=_settings_local(tmp_path))
        assert await notifier.healthcheck() is True

    async def test_healthcheck_returns_false_on_os_error(
        self, tmp_path: Path
    ) -> None:
        notifier = LocalFileNotifier(settings=_settings_local(tmp_path))
        with patch.object(notifier, "_ensure_dir", side_effect=OSError("permission denied")):
            assert await notifier.healthcheck() is False

    async def test_directory_is_created_automatically(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "c"
        settings = Settings(
            notification_channel="local_file",
            notification_local_dir=str(nested),
        )
        notifier = LocalFileNotifier(settings=settings)
        result = await notifier.send_alert(_make_alert_notification())

        assert result.success is True
        assert nested.exists()


# ---------------------------------------------------------------------------
# GoogleSheetsNotifier
# ---------------------------------------------------------------------------


def _settings_sheets() -> Settings:
    return Settings(
        notification_channel="google_sheets",
        google_service_account_json="/fake/sa.json",
        google_sheet_id="fake-sheet-id",
    )


def _make_mock_client(row_range: str = "Sheet1!A2") -> MagicMock:
    """gspread クライアントのモックを組み立てる。"""
    mock_worksheet = MagicMock()
    mock_worksheet.append_row.return_value = {
        "updates": {"updatedRange": row_range}
    }

    mock_spreadsheet = MagicMock()
    mock_spreadsheet.worksheet.return_value = mock_worksheet

    mock_client = MagicMock()
    mock_client.open_by_key.return_value = mock_spreadsheet

    return mock_client, mock_spreadsheet, mock_worksheet


class TestGoogleSheetsNotifier:
    async def test_send_daily_report_success(self) -> None:
        settings = _settings_sheets()
        mock_client, _, mock_worksheet = _make_mock_client("daily_reports!A2")
        notifier = GoogleSheetsNotifier(settings=settings, _client=mock_client)

        result = await notifier.send_daily_report_invite(_make_report_notification())

        assert result.success is True
        assert result.channel == "google_sheets"
        assert "daily_reports" in (result.message_id or "")
        mock_worksheet.append_row.assert_called_once()

    async def test_send_alert_success(self) -> None:
        settings = _settings_sheets()
        mock_client, _, mock_worksheet = _make_mock_client("alerts!A2")
        notifier = GoogleSheetsNotifier(settings=settings, _client=mock_client)

        result = await notifier.send_alert(_make_alert_notification())

        assert result.success is True
        assert result.channel == "google_sheets"
        mock_worksheet.append_row.assert_called_once()
        # 行データに severity が含まれることを確認
        call_args = mock_worksheet.append_row.call_args[0][0]
        assert "high" in call_args

    async def test_send_daily_report_creates_sheet_when_missing(self) -> None:
        """シートが存在しない場合に新規作成してヘッダ行を追加することを確認。"""
        settings = _settings_sheets()
        mock_worksheet = MagicMock()
        mock_worksheet.append_row.return_value = {"updates": {"updatedRange": "A2"}}

        mock_spreadsheet = MagicMock()
        # worksheet() が WorksheetNotFound を模倣する例外を送出
        mock_spreadsheet.worksheet.side_effect = Exception("WorksheetNotFound")
        mock_spreadsheet.add_worksheet.return_value = mock_worksheet

        mock_client = MagicMock()
        mock_client.open_by_key.return_value = mock_spreadsheet

        notifier = GoogleSheetsNotifier(settings=settings, _client=mock_client)
        result = await notifier.send_daily_report_invite(_make_report_notification())

        assert result.success is True
        mock_spreadsheet.add_worksheet.assert_called_once()
        # ヘッダ行 + データ行の 2 回呼ばれる
        assert mock_worksheet.append_row.call_count == 2

    async def test_returns_failure_when_sheet_id_missing(self) -> None:
        settings = Settings(
            notification_channel="google_sheets",
            google_service_account_json="/fake/sa.json",
            google_sheet_id="",
        )
        mock_client = MagicMock()
        notifier = GoogleSheetsNotifier(settings=settings, _client=mock_client)

        result = await notifier.send_alert(_make_alert_notification())

        assert result.success is False
        assert "GOOGLE_SHEET_ID" in (result.error or "")

    async def test_returns_failure_when_gspread_raises(self) -> None:
        settings = _settings_sheets()
        mock_client = MagicMock()
        mock_client.open_by_key.side_effect = Exception("API エラー")
        notifier = GoogleSheetsNotifier(settings=settings, _client=mock_client)

        result = await notifier.send_alert(_make_alert_notification())

        assert result.success is False

    async def test_gspread_import_error_raises_notification_error(self) -> None:
        """gspread が未インストールの場合に NotificationError を送出することを確認。"""
        settings = _settings_sheets()
        notifier = GoogleSheetsNotifier(settings=settings, _client=None)

        with patch(
            "src.infrastructure.notifiers.google_sheets._import_gspread",
            side_effect=NotificationError("gspread がインストールされていません。"),
        ):
            result = await notifier.send_alert(_make_alert_notification())

        assert result.success is False
        assert "gspread" in (result.error or "")

    async def test_healthcheck_returns_true_when_client_works(self) -> None:
        settings = _settings_sheets()
        mock_client = MagicMock()
        mock_client.open_by_key.return_value = MagicMock()
        notifier = GoogleSheetsNotifier(settings=settings, _client=mock_client)

        assert await notifier.healthcheck() is True

    async def test_healthcheck_returns_false_when_sheet_id_missing(self) -> None:
        settings = Settings(
            notification_channel="google_sheets",
            google_service_account_json="/fake/sa.json",
            google_sheet_id="",
        )
        mock_client = MagicMock()
        notifier = GoogleSheetsNotifier(settings=settings, _client=mock_client)

        assert await notifier.healthcheck() is False

    async def test_healthcheck_returns_false_on_exception(self) -> None:
        settings = _settings_sheets()
        mock_client = MagicMock()
        mock_client.open_by_key.side_effect = Exception("接続失敗")
        notifier = GoogleSheetsNotifier(settings=settings, _client=mock_client)

        assert await notifier.healthcheck() is False


# ---------------------------------------------------------------------------
# factory — notification_channel 選択とフォールバック
# ---------------------------------------------------------------------------


class TestBuildNotifierChannelSelection:
    # --- 後方互換 (既存テスト相当) ---

    def test_slack_channel_with_token_returns_slack(self) -> None:
        settings = Settings(
            notification_channel="slack",
            slack_bot_token="xoxb-test",
        )
        notifier = build_notifier(settings)
        assert isinstance(notifier, SlackNotifier)

    def test_slack_channel_without_token_returns_in_memory(self) -> None:
        """後方互換: slack_bot_token が空なら InMemoryNotifier を返す。"""
        settings = Settings(notification_channel="slack", slack_bot_token="")
        notifier = build_notifier(settings)
        assert isinstance(notifier, InMemoryNotifier)

    # --- 新チャンネル ---

    def test_local_file_channel_returns_local_file_notifier(
        self, tmp_path: Path
    ) -> None:
        settings = Settings(
            notification_channel="local_file",
            notification_local_dir=str(tmp_path),
        )
        notifier = build_notifier(settings)
        assert isinstance(notifier, LocalFileNotifier)

    def test_in_memory_channel_returns_in_memory(self) -> None:
        settings = Settings(notification_channel="in_memory")
        notifier = build_notifier(settings)
        assert isinstance(notifier, InMemoryNotifier)

    def test_google_sheets_channel_with_config_returns_sheets_notifier(self) -> None:
        settings = Settings(
            notification_channel="google_sheets",
            google_service_account_json="/fake/sa.json",
            google_sheet_id="fake-id",
        )
        notifier = build_notifier(settings)
        assert isinstance(notifier, GoogleSheetsNotifier)

    # --- フォールバック ---

    def test_google_sheets_without_config_falls_back_to_local_file(self) -> None:
        settings = Settings(
            notification_channel="google_sheets",
            google_service_account_json="",
            google_sheet_id="",
        )
        notifier = build_notifier(settings)
        assert isinstance(notifier, LocalFileNotifier)

    def test_google_sheets_without_sheet_id_falls_back_to_local_file(self) -> None:
        settings = Settings(
            notification_channel="google_sheets",
            google_service_account_json="/fake/sa.json",
            google_sheet_id="",
        )
        notifier = build_notifier(settings)
        assert isinstance(notifier, LocalFileNotifier)

    def test_unknown_channel_falls_back_to_local_file(self) -> None:
        settings = Settings(notification_channel="email")
        notifier = build_notifier(settings)
        assert isinstance(notifier, LocalFileNotifier)

    def test_local_file_notifier_has_correct_dir(self, tmp_path: Path) -> None:
        settings = Settings(
            notification_channel="local_file",
            notification_local_dir=str(tmp_path / "out"),
        )
        notifier = build_notifier(settings)
        assert isinstance(notifier, LocalFileNotifier)
        assert notifier.settings.notification_local_dir == str(tmp_path / "out")

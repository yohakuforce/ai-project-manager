"""
TrackService — 動的日報フォーマット生成・配信・回答解析 Application Service。

責務:
  1. generate_daily_report_templates: 当日のタスク状態に基づいて各メンバーの日報テンプレートを生成
  2. deliver_reports: 生成済みテンプレートをメンバーへ配信（配信済みマーク）
  3. submit_responses: メンバーの回答を受信して DailyReport に記録
  4. analyze_responses: AI が回答を解析してサマリと警告フラグを付与
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from src.domain.audit.aggregate import AuditAction, AuditLog
from src.domain.audit.repository import AuditLogRepository
from src.domain.member.aggregate import Member
from src.domain.member.repository import MemberRepository
from src.domain.member.value_objects import MemberId
from src.domain.project.aggregate import Project
from src.domain.project.entities import Task
from src.domain.project.repository import ProjectRepository
from src.domain.project.value_objects import ProjectId, TaskStatus
from src.domain.reporting.aggregate import DailyReport
from src.domain.reporting.repository import DailyReportRepository
from src.domain.reporting.value_objects import (
    DailyReportId,
    QuestionId,
    QuestionType,
    ReportQuestion,
    ReportStatus,
    ReportTemplate,
)
from src.infrastructure.llm.adapter import LLMAdapter
from src.infrastructure.notifiers.protocol import (
    DailyReportNotification,
    MessageNotification,
    NotificationError,
    Notifier,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerateTemplatesResult:
    """generate_daily_report_templates の戻り値。"""

    project_id: str
    report_date: str
    reports_generated: int
    report_ids: list[str]


@dataclass(frozen=True)
class DeliverReportsResult:
    """deliver_reports の戻り値。"""

    project_id: str
    report_date: str
    reports_delivered: int
    notifications_sent: int = 0
    notifications_failed: int = 0


@dataclass(frozen=True)
class RemindResult:
    """remind_unsubmitted の戻り値。"""

    project_id: str
    report_date: str
    unsubmitted_member_ids: list[str]
    member_reminders_sent: int
    leader_notified: bool


@dataclass(frozen=True)
class ResponseInput:
    """submit_responses へのメンバー回答入力。"""

    question_id: str
    response_text: str


@dataclass(frozen=True)
class SubmitResponsesResult:
    """submit_responses の戻り値。"""

    report_id: str
    responses_saved: int
    is_complete: bool


@dataclass(frozen=True)
class AnalyzeResult:
    """analyze_responses の戻り値。"""

    report_id: str
    ai_summary: str
    blockers_detected: list[str]


class TrackService:
    """
    日報フォーマット生成・配信・解析 Application Service。
    """

    def __init__(
        self,
        project_repository: ProjectRepository,
        member_repository: MemberRepository,
        daily_report_repository: DailyReportRepository,
        llm_adapter: LLMAdapter,
        notifier: Notifier | None = None,
        default_channel: str = "",
        leader_channel: str = "",
        audit_repository: AuditLogRepository | None = None,
    ) -> None:
        self._project_repo = project_repository
        self._member_repo = member_repository
        self._report_repo = daily_report_repository
        self._llm = llm_adapter
        self._notifier = notifier
        self._default_channel = default_channel
        # リーダー（PL/PM）向け共有チャネル。未指定なら default_channel を流用。
        self._leader_channel = leader_channel or default_channel
        self._audit_repo = audit_repository

    async def generate_daily_report_templates(
        self,
        project_id: str,
        report_date: date | None = None,
    ) -> GenerateTemplatesResult:
        """
        プロジェクトの全メンバーに対して日報テンプレートを生成する。
        既に当日の DailyReport が存在するメンバーはスキップ。

        Args:
            project_id: 対象プロジェクト
            report_date: 対象日（None の場合は今日）
        """
        target_date = report_date or date.today()

        project = await self._project_repo.find_by_id(ProjectId.from_str(project_id))
        if project is None:
            raise ValueError(f"Project が見つかりません: {project_id}")

        members = await self._member_repo.find_all()
        active_tasks = project.active_tasks()
        created_ids: list[str] = []

        for member in members:
            existing = await self._report_repo.find_by_member_and_date(
                str(member.member_id), target_date
            )
            if existing is not None:
                logger.debug(
                    "既存レポートをスキップ: member=%s date=%s", member.member_id, target_date
                )
                continue

            member_tasks = self._get_member_tasks(member, project, active_tasks)
            template = await self._build_template(member, member_tasks, target_date)
            report = DailyReport(
                report_id=DailyReportId.generate(),
                member_id=str(member.member_id),
                project_id=project_id,
                report_date=target_date,
                template=template,
            )
            await self._report_repo.save(report)
            created_ids.append(str(report.report_id))
            logger.info(
                "日報テンプレート生成",
                extra={
                    "report_id": str(report.report_id),
                    "member_id": str(member.member_id),
                    "member_name": member.name,
                    "questions": len(template.questions),
                },
            )

        return GenerateTemplatesResult(
            project_id=project_id,
            report_date=target_date.isoformat(),
            reports_generated=len(created_ids),
            report_ids=created_ids,
        )

    async def deliver_reports(
        self,
        project_id: str,
        report_date: date | None = None,
    ) -> DeliverReportsResult:
        """
        PENDING 状態の日報をすべて配信済みにマークする。
        実際の Slack 配信は Slack Bot 層（次セッション）が担当。
        このメソッドは配信済みフラグを立てる。
        """
        target_date = report_date or date.today()
        pending_reports = await self._report_repo.find_by_status(project_id, ReportStatus.PENDING)
        count = 0
        notifications_sent = 0
        notifications_failed = 0
        for report in pending_reports:
            if report.report_date != target_date:
                continue
            report.mark_delivered()
            await self._report_repo.save(report)
            count += 1
            await self._record_audit(
                action=AuditAction.REPORT_DELIVERED,
                actor="system",
                project_id=project_id,
                data_ref=str(report.report_id),
            )

            if self._notifier is None:
                continue

            try:
                member_id = MemberId.from_str(report.member_id)
            except (ValueError, AttributeError):
                logger.warning(
                    "メンバー ID の解釈に失敗しました: member_id=%s report_id=%s",
                    report.member_id,
                    report.report_id,
                )
                notifications_failed += 1
                continue

            member = await self._member_repo.find_by_id(member_id)
            if member is None:
                logger.warning(
                    "通知対象メンバーが見つかりません: member_id=%s report_id=%s",
                    report.member_id,
                    report.report_id,
                )
                notifications_failed += 1
                continue

            channel = self._resolve_member_channel(member)
            if not channel:
                logger.warning(
                    "メンバーの通知チャンネルが未設定です: member_id=%s",
                    report.member_id,
                )
                notifications_failed += 1
                continue

            payload = DailyReportNotification(
                report=report,
                member_name=member.name,
                member_channel=channel,
            )
            try:
                result = await self._notifier.send_daily_report_invite(payload)
            except NotificationError as exc:
                logger.error(
                    "日報通知の配信に失敗しました: report_id=%s error=%s",
                    report.report_id,
                    exc,
                )
                notifications_failed += 1
                continue

            if result.success:
                notifications_sent += 1
            else:
                notifications_failed += 1
                logger.warning(
                    "日報通知が success=False を返しました: report_id=%s error=%s",
                    report.report_id,
                    result.error,
                )

        return DeliverReportsResult(
            project_id=project_id,
            report_date=target_date.isoformat(),
            reports_delivered=count,
            notifications_sent=notifications_sent,
            notifications_failed=notifications_failed,
        )

    async def remind_unsubmitted(
        self,
        project_id: str,
        report_date: date | None = None,
    ) -> RemindResult:
        """日報未提出者に催促する（17時想定）。

        - 未提出（PENDING / DELIVERED）の各メンバー本人へ DM 催促を送る。
        - リーダー（共有チャネル）へ未提出者一覧を提示する。
        どちらも notifier が無い場合は送信せず件数 0 で返す（落とさない）。
        """
        target_date = report_date or date.today()
        reports = await self._report_repo.find_by_project_and_date(project_id, target_date)
        unsubmitted = [
            r for r in reports if r.status in (ReportStatus.PENDING, ReportStatus.DELIVERED)
        ]

        unsubmitted_member_ids = [r.member_id for r in unsubmitted]
        if not unsubmitted:
            logger.info("未提出の日報はありません: project=%s date=%s", project_id, target_date)
            return RemindResult(
                project_id=project_id,
                report_date=target_date.isoformat(),
                unsubmitted_member_ids=[],
                member_reminders_sent=0,
                leader_notified=False,
            )

        if self._notifier is None:
            return RemindResult(
                project_id=project_id,
                report_date=target_date.isoformat(),
                unsubmitted_member_ids=unsubmitted_member_ids,
                member_reminders_sent=0,
                leader_notified=False,
            )

        # --- 本人への DM 催促 ---
        member_reminders_sent = 0
        unsubmitted_names: list[str] = []
        for report in unsubmitted:
            member = await self._find_member(report.member_id)
            member_name = member.name if member is not None else report.member_id
            unsubmitted_names.append(member_name)

            channel = self._resolve_member_channel(member) if member is not None else ""
            if not channel:
                continue
            payload = MessageNotification(
                channel=channel,
                title=f"日報の提出をお願いします（{target_date.isoformat()}）",
                body=(
                    f"{member_name} さん、本日（{target_date.isoformat()}）の日報が未提出です。"
                    "お手すきの際にご提出ください。"
                ),
                kind="reminder",
            )
            if await self._safe_send_message(payload):
                member_reminders_sent += 1

        # --- リーダーへ未提出者一覧 ---
        leader_notified = False
        if self._leader_channel:
            name_lines = "\n".join(f"・{name}" for name in unsubmitted_names)
            payload = MessageNotification(
                channel=self._leader_channel,
                title=f"日報未提出者（{target_date.isoformat()}）{len(unsubmitted_names)}名",
                body=f"以下のメンバーが未提出です。\n{name_lines}",
                kind="reminder",
            )
            leader_notified = await self._safe_send_message(payload)

        return RemindResult(
            project_id=project_id,
            report_date=target_date.isoformat(),
            unsubmitted_member_ids=unsubmitted_member_ids,
            member_reminders_sent=member_reminders_sent,
            leader_notified=leader_notified,
        )

    async def _find_member(self, member_id: str):
        """member_id 文字列から Member を解決する。不正・不在なら None。"""
        try:
            return await self._member_repo.find_by_id(MemberId.from_str(member_id))
        except (ValueError, AttributeError):
            return None

    async def _safe_send_message(self, payload: MessageNotification) -> bool:
        """send_message を例外安全に呼ぶ。成功なら True。"""
        if self._notifier is None:
            return False
        try:
            result = await self._notifier.send_message(payload)
        except NotificationError as exc:
            logger.error("メッセージ通知に失敗しました: channel=%s error=%s", payload.channel, exc)
            return False
        return bool(result.success)

    async def _record_audit(
        self,
        *,
        action: AuditAction,
        actor: str,
        project_id: str | None,
        data_ref: str | None,
    ) -> None:
        """監査ログを記録する。失敗してもメインフローは止めない。"""
        if self._audit_repo is None:
            return
        try:
            await self._audit_repo.append(
                AuditLog.create(
                    actor=actor,
                    action=action,
                    project_id=project_id,
                    data_ref=data_ref,
                )
            )
        except Exception as exc:  # pragma: no cover - 想定外の I/O 失敗
            logger.warning("監査ログ記録に失敗しました: action=%s error=%s", action, exc)

    def _resolve_member_channel(self, member: Member) -> str:
        """メンバーの通知チャンネルを解決する。

        現状: ``external_id`` を Slack 側の channel id として扱う（Slack DM の場合は
        ユーザー ID で送信可能）。空ならフォールバックとして ``default_channel``。
        """
        if member.external_id:
            return member.external_id
        return self._default_channel

    async def submit_responses(
        self,
        report_id: str,
        responses: list[ResponseInput],
        finalize: bool = True,
    ) -> SubmitResponsesResult:
        """
        メンバーの回答を DailyReport に記録する。

        Args:
            report_id: 対象日報 ID
            responses: 回答リスト
            finalize: True の場合に finalize_submission() を呼んで SUBMITTED に変更
        """
        import uuid

        report = await self._report_repo.find_by_id(DailyReportId(value=uuid.UUID(report_id)))
        if report is None:
            raise ValueError(f"DailyReport が見つかりません: {report_id}")

        for resp in responses:
            report.submit_response(
                question_id=QuestionId(value=__import__("uuid").UUID(resp.question_id)),
                response_text=resp.response_text,
            )

        if finalize:
            report.finalize_submission()
            await self._record_audit(
                action=AuditAction.REPORT_SUBMITTED,
                actor=report.member_id,
                project_id=report.project_id,
                data_ref=str(report.report_id),
            )

        await self._report_repo.save(report)

        return SubmitResponsesResult(
            report_id=report_id,
            responses_saved=len(responses),
            is_complete=report.is_fully_answered(),
        )

    async def analyze_responses(self, report_id: str) -> AnalyzeResult:
        """
        AI が DailyReport の回答を解析してサマリとブロッカーを検出する。

        Args:
            report_id: 解析対象の日報 ID

        Returns:
            AnalyzeResult（サマリと検出されたブロッカーリスト）
        """
        import uuid

        report = await self._report_repo.find_by_id(DailyReportId(value=uuid.UUID(report_id)))
        if report is None:
            raise ValueError(f"DailyReport が見つかりません: {report_id}")

        if report.status not in (ReportStatus.SUBMITTED, ReportStatus.DELIVERED):
            raise ValueError(
                f"解析できないステータスです: {report.status}。SUBMITTED または DELIVERED である必要があります。"
            )

        prompt = self._build_analysis_prompt(report)
        try:
            response = await self._llm.generate(prompt, max_tokens=500, temperature=0.2)
            raw_text = response.content.strip()
        except Exception as exc:
            logger.error("LLM 解析失敗: %s", exc)
            raw_text = "AI 解析に失敗しました。"

        summary, blockers = self._parse_analysis_output(raw_text, report)
        report.set_ai_summary(summary)
        await self._report_repo.save(report)

        return AnalyzeResult(
            report_id=report_id,
            ai_summary=summary,
            blockers_detected=blockers,
        )

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def _get_member_tasks(
        member: Member,
        project: Project,
        active_tasks: list[Task],
    ) -> list[Task]:
        """メンバーに割り当てられているアクティブタスクを返す。"""
        confirmed = {
            str(a.task_id)
            for a in project.confirmed_assignments()
            if a.member_id == str(member.member_id)
        }
        return [t for t in active_tasks if str(t.task_id) in confirmed]

    async def _build_template(
        self,
        member: Member,
        member_tasks: list[Task],
        report_date: date,
    ) -> ReportTemplate:
        """タスク状態に基づいて動的テンプレートを構築する。"""
        questions: list[ReportQuestion] = []

        if not member_tasks:
            # タスクなし: 自由記述のみ
            questions.append(
                ReportQuestion(
                    question_id=QuestionId.generate(),
                    question_type=QuestionType.FREE_TEXT,
                    body="本日の作業内容と成果を記入してください。",
                )
            )
            return ReportTemplate.create(questions)

        # タスクごとの質問を生成
        for task in member_tasks:
            questions.append(
                ReportQuestion(
                    question_id=QuestionId.generate(),
                    question_type=QuestionType.PROGRESS_PERCENT,
                    task_id=str(task.task_id),
                    body=f"「{task.title}」の進捗率（0〜100）を入力してください。",
                )
            )
            questions.append(
                ReportQuestion(
                    question_id=QuestionId.generate(),
                    question_type=QuestionType.BLOCKER,
                    task_id=str(task.task_id),
                    body=f"「{task.title}」にブロッカーや課題がある場合は記入してください（なければ「なし」）。",
                )
            )
            if task.status == TaskStatus.BLOCKED:
                questions.append(
                    ReportQuestion(
                        question_id=QuestionId.generate(),
                        question_type=QuestionType.CUSTOMER_PENDING,
                        task_id=str(task.task_id),
                        body=f"「{task.title}」のブロック解除のために顧客への確認が必要ですか？",
                    )
                )

        # 全体フリーテキスト
        questions.append(
            ReportQuestion(
                question_id=QuestionId.generate(),
                question_type=QuestionType.FREE_TEXT,
                body="その他、共有事項があれば記入してください。",
            )
        )

        return ReportTemplate.create(questions)

    @staticmethod
    def _build_analysis_prompt(report: DailyReport) -> str:
        """解析用プロンプトを構築する。"""
        lines = [
            f"日報解析: {report.report_date}",
            f"メンバーID: {report.member_id}",
            "",
            "以下の回答を解析し、日本語で次の2点を回答してください:",
            "1. 進捗サマリ（2〜3文）",
            "2. ブロッカーリスト（「なし」または箇条書き）",
            "",
            "--- 回答 ---",
        ]
        for response in report.responses:
            # 質問IDに対応する質問本文を探す
            question_body = "（質問不明）"
            for q in report.template.questions:
                if q.question_id == response.question_id:
                    question_body = q.body
                    break
            lines.append(f"Q: {question_body}")
            lines.append(f"A: {response.response_text}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _parse_analysis_output(raw_text: str, report: DailyReport) -> tuple[str, list[str]]:
        """AI 出力からサマリとブロッカーを抽出する。"""
        blockers: list[str] = []
        summary = raw_text

        # ブロッカー回答からシンプルに抽出
        for response in report.responses:
            for q in report.template.questions:
                if (
                    q.question_id == response.question_id
                    and q.question_type == QuestionType.BLOCKER
                    and response.response_text.strip().lower() not in ("なし", "none", "")
                ):
                    blockers.append(response.response_text.strip())

        return summary, blockers

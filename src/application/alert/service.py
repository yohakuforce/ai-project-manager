"""
AlertService — 遅延・過負荷・応答遅延・パターン認識 Application Service。

責務:
  - Task の期日遅延スキャン → TASK_DELAY アラート生成
  - メンバー過負荷スキャン（未完了タスク数・稼働時間） → MEMBER_OVERLOAD アラート生成
  - 日報未回答スキャン → CUSTOMER_NO_RESPONSE / パターン認識
  - acknowledge / resolve 操作
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from src.domain.alert.aggregate import (
    Alert,
    AlertCategory,
    AlertId,
    AlertSeverity,
    Evidence,
    EvidenceType,
)
from src.domain.alert.repository import AlertRepository
from src.domain.audit.aggregate import AuditAction, AuditLog
from src.domain.audit.repository import AuditLogRepository
from src.domain.member.repository import MemberRepository
from src.domain.project.aggregate import Project
from src.domain.project.repository import ProjectRepository
from src.domain.project.value_objects import ProjectId
from src.domain.reporting.repository import DailyReportRepository
from src.domain.reporting.value_objects import ReportStatus
from src.infrastructure.llm.adapter import LLMAdapter
from src.infrastructure.notifiers.protocol import (
    AlertNotification,
    NotificationError,
    Notifier,
)

logger = logging.getLogger(__name__)

# スキャンの閾値定数
_DELAY_WARNING_DAYS = 0  # 期日超過で即アラート
_OVERLOAD_TASK_COUNT = 5  # 未完了タスク数閾値
_OVERLOAD_ESTIMATED_HOURS = 40.0  # 推定時間合計閾値（週）
_REPORT_LATE_DAYS = 1  # 日報未回答が N 日で応答遅延アラート


@dataclass(frozen=True)
class ScanResult:
    """AlertService.scan_project の戻り値。"""

    project_id: str
    scan_date: str
    alerts_created: int
    alert_ids: list[str]
    alert_categories: list[str]
    notifications_sent: int = 0
    notifications_failed: int = 0


@dataclass(frozen=True)
class AcknowledgeResult:
    """acknowledge_alert の戻り値。"""

    alert_id: str
    acknowledged_by: str


class AlertService:
    """
    Alert 生成・管理 Application Service。
    """

    def __init__(
        self,
        project_repository: ProjectRepository,
        member_repository: MemberRepository,
        alert_repository: AlertRepository,
        daily_report_repository: DailyReportRepository,
        llm_adapter: LLMAdapter,
        notifier: Notifier | None = None,
        alert_channel: str = "#ai-pm-alerts",
        audit_repository: AuditLogRepository | None = None,
    ) -> None:
        self._project_repo = project_repository
        self._member_repo = member_repository
        self._alert_repo = alert_repository
        self._report_repo = daily_report_repository
        self._llm = llm_adapter
        self._notifier = notifier
        self._alert_channel = alert_channel
        self._audit_repo = audit_repository

    async def scan_project(
        self,
        project_id: str,
        scan_date: date | None = None,
    ) -> ScanResult:
        """
        プロジェクトをスキャンして各種アラート条件を判定し、
        新規 Alert を生成・保存する。

        スキャン項目:
          1. タスク遅延（TASK_DELAY）
          2. メンバー過負荷（MEMBER_OVERLOAD）
          3. 日報未回答（CUSTOMER_NO_RESPONSE）
        """
        check_date = scan_date or date.today()

        project = await self._project_repo.find_by_id(ProjectId.from_str(project_id))
        if project is None:
            raise ValueError(f"Project が見つかりません: {project_id}")

        members = await self._member_repo.find_by_project_id(ProjectId.from_str(project_id))
        created_ids: list[str] = []
        created_categories: list[str] = []
        new_alerts: list[Alert] = []

        # --- 1. タスク遅延スキャン ---
        delay_alerts = await self._scan_task_delays(project, check_date)
        for alert in delay_alerts:
            await self._alert_repo.save(alert)
            created_ids.append(str(alert.alert_id))
            created_categories.append(alert.category.value)
            new_alerts.append(alert)

        # --- 2. メンバー過負荷スキャン ---
        overload_alerts = await self._scan_member_overload(project, members, check_date)
        for alert in overload_alerts:
            await self._alert_repo.save(alert)
            created_ids.append(str(alert.alert_id))
            created_categories.append(alert.category.value)
            new_alerts.append(alert)

        # --- 3. 日報未回答スキャン ---
        no_response_alerts = await self._scan_report_no_response(project_id, members, check_date)
        for alert in no_response_alerts:
            await self._alert_repo.save(alert)
            created_ids.append(str(alert.alert_id))
            created_categories.append(alert.category.value)
            new_alerts.append(alert)

        notifications_sent, notifications_failed = await self._notify_new_alerts(
            new_alerts, project, members
        )

        for alert in new_alerts:
            await self._record_audit(
                action=AuditAction.ALERT_CREATED,
                actor="system",
                project_id=project_id,
                data_ref=str(alert.alert_id),
            )

        logger.info(
            "Alert スキャン完了",
            extra={
                "project_id": project_id,
                "scan_date": check_date.isoformat(),
                "alerts_created": len(created_ids),
                "notifications_sent": notifications_sent,
                "notifications_failed": notifications_failed,
            },
        )

        return ScanResult(
            project_id=project_id,
            scan_date=check_date.isoformat(),
            alerts_created=len(created_ids),
            alert_ids=created_ids,
            alert_categories=list(set(created_categories)),
            notifications_sent=notifications_sent,
            notifications_failed=notifications_failed,
        )

    async def _notify_new_alerts(
        self,
        alerts: list[Alert],
        project: Project,
        members: list,
    ) -> tuple[int, int]:
        """生成された Alert を Notifier 経由で配信する。失敗してもスキャン処理は止めない。"""
        if self._notifier is None or not alerts:
            return 0, 0

        member_name_by_id = {str(m.member_id): m.name for m in members}
        sent = 0
        failed = 0
        for alert in alerts:
            target_name = (
                member_name_by_id.get(alert.target_member_id) if alert.target_member_id else None
            )
            payload = AlertNotification(
                alert=alert,
                project_name=project.name,
                recipient_channel=self._alert_channel,
                target_member_name=target_name,
            )
            try:
                result = await self._notifier.send_alert(payload)
            except NotificationError as exc:
                logger.error(
                    "アラート通知の配信に失敗しました: alert_id=%s error=%s",
                    alert.alert_id,
                    exc,
                )
                failed += 1
                continue

            if result.success:
                sent += 1
            else:
                failed += 1
                logger.warning(
                    "アラート通知が success=False を返しました: alert_id=%s error=%s",
                    alert.alert_id,
                    result.error,
                )

        return sent, failed

    async def acknowledge_alert(self, alert_id: str, acknowledged_by: str) -> AcknowledgeResult:
        """PL/PM がアラートを確認する。"""
        import uuid

        from src.domain.alert.aggregate import AlertId

        aid = AlertId(value=uuid.UUID(alert_id))
        alert = await self._alert_repo.find_by_id(aid)
        if alert is None:
            raise ValueError(f"Alert が見つかりません: {alert_id}")

        alert.acknowledge(acknowledged_by)
        await self._alert_repo.save(alert)
        await self._record_audit(
            action=AuditAction.ALERT_ACKNOWLEDGED,
            actor=acknowledged_by,
            project_id=alert.project_id,
            data_ref=alert_id,
        )
        return AcknowledgeResult(alert_id=alert_id, acknowledged_by=acknowledged_by)

    async def resolve_alert(self, alert_id: str) -> None:
        """アラートを解決済みにする。"""
        import uuid

        from src.domain.alert.aggregate import AlertId

        aid = AlertId(value=uuid.UUID(alert_id))
        alert = await self._alert_repo.find_by_id(aid)
        if alert is None:
            raise ValueError(f"Alert が見つかりません: {alert_id}")

        alert.resolve()
        await self._alert_repo.save(alert)

    # ------------------------------------------------------------------
    # スキャンロジック
    # ------------------------------------------------------------------

    async def _scan_task_delays(self, project: Project, check_date: date) -> list[Alert]:
        """期日超過タスクを検出して Alert を生成する。"""
        alerts: list[Alert] = []
        existing_active = await self._alert_repo.find_active_by_project(str(project.project_id))
        existing_task_ids = {
            a.target_task_id for a in existing_active if a.category == AlertCategory.TASK_DELAY
        }

        for task in project.active_tasks():
            if str(task.task_id) in existing_task_ids:
                continue  # 既存のアクティブアラートがある

            if task.due_date is None:
                continue

            delay_days = (check_date - task.due_date).days
            if delay_days < _DELAY_WARNING_DAYS:
                continue

            severity = (
                AlertSeverity.CRITICAL
                if delay_days >= 3
                else AlertSeverity.HIGH
                if delay_days >= 1
                else AlertSeverity.MEDIUM
            )

            message = await self._generate_alert_message(
                f"タスク「{task.title}」が{delay_days}日遅延しています。"
                f"（期日: {task.due_date}、現在: {check_date}）"
            )

            alerts.append(
                Alert(
                    alert_id=AlertId.generate(),
                    project_id=str(project.project_id),
                    category=AlertCategory.TASK_DELAY,
                    severity=severity,
                    ai_generated_message=message,
                    evidence=[
                        Evidence(
                            evidence_type=EvidenceType.TASK_STATUS,
                            data_ref=str(task.task_id),
                            human_readable_summary=(
                                f"タスク「{task.title}」は期日 {task.due_date} を "
                                f"{delay_days}日超過しています。現在ステータス: {task.status.value}"
                            ),
                        )
                    ],
                    target_task_id=str(task.task_id),
                )
            )

        return alerts

    async def _scan_member_overload(
        self, project: Project, members: list, check_date: date
    ) -> list[Alert]:
        """メンバーの未完了タスク過多を検出してアラートを生成する。"""
        alerts: list[Alert] = []
        confirmed = project.confirmed_assignments()

        for member in members:
            member_task_ids = {
                str(a.task_id) for a in confirmed if a.member_id == str(member.member_id)
            }
            member_active_tasks = [
                t for t in project.active_tasks() if str(t.task_id) in member_task_ids
            ]

            if len(member_active_tasks) < _OVERLOAD_TASK_COUNT:
                continue

            total_hours = sum(t.estimated_hours or 0.0 for t in member_active_tasks)

            existing_active = await self._alert_repo.find_active_by_project(str(project.project_id))
            already_alerted = any(
                a.category == AlertCategory.MEMBER_OVERLOAD
                and a.target_member_id == str(member.member_id)
                for a in existing_active
            )
            if already_alerted:
                continue

            message = await self._generate_alert_message(
                f"{member.name}の未完了タスクが{len(member_active_tasks)}件あります。"
                f"推定残作業時間合計: {total_hours}時間"
            )

            alerts.append(
                Alert(
                    alert_id=AlertId.generate(),
                    project_id=str(project.project_id),
                    category=AlertCategory.MEMBER_OVERLOAD,
                    severity=AlertSeverity.HIGH,
                    ai_generated_message=message,
                    evidence=[
                        Evidence(
                            evidence_type=EvidenceType.TASK_STATUS,
                            data_ref=str(member.member_id),
                            human_readable_summary=(
                                f"{member.name}（{member.role.value}）の未完了タスク数: "
                                f"{len(member_active_tasks)}件。推定時間: {total_hours}時間"
                            ),
                        )
                    ],
                    target_member_id=str(member.member_id),
                )
            )

        return alerts

    async def _scan_report_no_response(
        self, project_id: str, members: list, check_date: date
    ) -> list[Alert]:
        """日報未回答メンバーを検出してアラートを生成する。"""
        alerts: list[Alert] = []
        threshold_date = check_date - timedelta(days=_REPORT_LATE_DAYS)

        reports = await self._report_repo.find_by_project_and_date(project_id, threshold_date)
        pending_member_ids = {
            r.member_id
            for r in reports
            if r.status in (ReportStatus.PENDING, ReportStatus.DELIVERED)
        }

        for member_id in pending_member_ids:
            member_name = next(
                (m.name for m in members if str(m.member_id) == member_id),
                member_id,
            )
            message = await self._generate_alert_message(
                f"{member_name}が{threshold_date}の日報を未提出です。"
            )
            alerts.append(
                Alert(
                    alert_id=AlertId.generate(),
                    project_id=project_id,
                    category=AlertCategory.CUSTOMER_NO_RESPONSE,
                    severity=AlertSeverity.MEDIUM,
                    ai_generated_message=message,
                    evidence=[
                        Evidence(
                            evidence_type=EvidenceType.REPORT_RESPONSE,
                            data_ref=member_id,
                            human_readable_summary=(
                                f"{member_name}の{threshold_date}日報が未回答です。"
                            ),
                        )
                    ],
                    target_member_id=member_id,
                )
            )

        return alerts

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

    async def _generate_alert_message(self, context: str) -> str:
        """LLM でアラートメッセージを生成する。失敗時は context をそのまま返す。"""
        prompt = (
            f"以下の状況について、PL/PM への日本語アラートメッセージを1文で生成してください。\n\n"
            f"状況: {context}\n\n"
            "アラートメッセージ:"
        )
        try:
            response = await self._llm.generate(prompt, max_tokens=150, temperature=0.3)
            return response.content.strip()
        except Exception as exc:
            logger.warning("LLM アラートメッセージ生成失敗: %s", exc)
            return context

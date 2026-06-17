"""
OverviewService — 日次サマリ + フェーズ進捗レポート Application Service。

責務:
  - 日次サマリ: 当日の Task 状態 + Alert サマリ + 日報提出状況を集約してレポートを生成
  - フェーズ進捗レポート: Phase の計画対実績（PhaseProgress）を計算してレポートを生成
  - LLM アダプタ経由で自然言語サマリを生成
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from src.domain.alert.repository import AlertRepository
from src.domain.member.repository import MemberRepository
from src.domain.project.repository import ProjectRepository
from src.domain.project.value_objects import ProjectId, TaskStatus
from src.domain.reporting.repository import DailyReportRepository
from src.domain.reporting.value_objects import ReportStatus
from src.infrastructure.llm.adapter import LLMAdapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskSummary:
    total: int
    pending: int
    in_progress: int
    blocked: int
    done: int
    overdue: int


@dataclass(frozen=True)
class ReportSummary:
    total_members: int
    submitted: int
    pending: int


@dataclass(frozen=True)
class AlertSummaryItem:
    category: str
    severity: str
    count: int


@dataclass(frozen=True)
class DailySummaryResult:
    """generate_daily_summary の戻り値。"""

    project_id: str
    summary_date: str
    task_summary: TaskSummary
    report_summary: ReportSummary
    alert_summary: list[AlertSummaryItem]
    ai_narrative: str


@dataclass(frozen=True)
class PhaseProgressItem:
    phase_id: str
    phase_name: str
    planned_end_date: str
    projected_end_date: str
    completion_rate: float
    deviation_days: int
    is_delayed: bool


@dataclass(frozen=True)
class PhaseProgressResult:
    """generate_phase_progress の戻り値。"""

    project_id: str
    as_of_date: str
    phases: list[PhaseProgressItem]
    overall_completion_rate: float
    ai_narrative: str


class OverviewService:
    """
    プロジェクト俯瞰レポート Application Service。
    """

    def __init__(
        self,
        project_repository: ProjectRepository,
        member_repository: MemberRepository,
        alert_repository: AlertRepository,
        daily_report_repository: DailyReportRepository,
        llm_adapter: LLMAdapter,
    ) -> None:
        self._project_repo = project_repository
        self._member_repo = member_repository
        self._alert_repo = alert_repository
        self._report_repo = daily_report_repository
        self._llm = llm_adapter

    async def generate_daily_summary(
        self,
        project_id: str,
        summary_date: date | None = None,
    ) -> DailySummaryResult:
        """
        日次サマリを生成する。

        集約内容:
          - Task ステータス分布
          - 期日超過タスク数
          - 日報提出状況
          - ACTIVE Alert サマリ
          - AI ナラティブサマリ
        """
        target_date = summary_date or date.today()

        project = await self._project_repo.find_by_id(ProjectId.from_str(project_id))
        if project is None:
            raise ValueError(f"Project が見つかりません: {project_id}")

        members = await self._member_repo.find_by_project_id(ProjectId.from_str(project_id))

        # Task 集計
        all_tasks = project.tasks
        task_summary = TaskSummary(
            total=len(all_tasks),
            pending=sum(1 for t in all_tasks if t.status == TaskStatus.PENDING),
            in_progress=sum(1 for t in all_tasks if t.status == TaskStatus.IN_PROGRESS),
            blocked=sum(1 for t in all_tasks if t.status == TaskStatus.BLOCKED),
            done=sum(1 for t in all_tasks if t.status == TaskStatus.DONE),
            overdue=sum(1 for t in all_tasks if t.is_overdue),
        )

        # 日報提出状況
        reports = await self._report_repo.find_by_project_and_date(project_id, target_date)
        submitted_count = sum(
            1 for r in reports if r.status in (ReportStatus.SUBMITTED, ReportStatus.ANALYZED)
        )
        report_summary = ReportSummary(
            total_members=len(members),
            submitted=submitted_count,
            pending=len(members) - submitted_count,
        )

        # Alert サマリ
        active_alerts = await self._alert_repo.find_active_by_project(project_id)
        alert_by_cat: dict[str, list] = {}
        for alert in active_alerts:
            key = f"{alert.category.value}_{alert.severity.value}"
            alert_by_cat.setdefault(key, []).append(alert)

        alert_summary = [
            AlertSummaryItem(
                category=alerts[0].category.value,
                severity=alerts[0].severity.value,
                count=len(alerts),
            )
            for alerts in alert_by_cat.values()
        ]

        # AI ナラティブ
        ai_narrative = await self._generate_daily_narrative(
            project.name, target_date, task_summary, report_summary, alert_summary
        )

        return DailySummaryResult(
            project_id=project_id,
            summary_date=target_date.isoformat(),
            task_summary=task_summary,
            report_summary=report_summary,
            alert_summary=alert_summary,
            ai_narrative=ai_narrative,
        )

    async def generate_phase_progress(
        self,
        project_id: str,
        as_of_date: date | None = None,
    ) -> PhaseProgressResult:
        """
        フェーズ進捗レポートを生成する。

        PhaseProgress 計算ロジック（シンプル実装）:
          - completion_rate = phase 内の DONE タスク数 / 全タスク数
          - projected_end_date = 現在の進捗ペースで計算
          - deviation_days = projected - planned
        """
        target_date = as_of_date or date.today()

        project = await self._project_repo.find_by_id(ProjectId.from_str(project_id))
        if project is None:
            raise ValueError(f"Project が見つかりません: {project_id}")

        phase_items: list[PhaseProgressItem] = []

        for phase in project.phases:
            # Phase 内タスク（sourceRef や依存関係でフィルタする本格実装は次フェーズ）
            # シンプル実装: 全タスクの進捗をフェーズ全体の進捗として扱う
            all_tasks = project.tasks
            if not all_tasks:
                completion_rate = 0.0
                projected_end = phase.planned_end_date
            else:
                done_count = sum(1 for t in all_tasks if t.status == TaskStatus.DONE)
                completion_rate = done_count / len(all_tasks)

                # 進捗ペースから完了予測日を計算
                elapsed_days = (target_date - phase.start_date).days
                if completion_rate > 0 and elapsed_days > 0:
                    total_estimated_days = elapsed_days / completion_rate
                    from datetime import timedelta

                    projected_end = phase.start_date + timedelta(days=int(total_estimated_days))
                else:
                    projected_end = phase.planned_end_date

            deviation_days = (projected_end - phase.planned_end_date).days

            phase_items.append(
                PhaseProgressItem(
                    phase_id=str(phase.phase_id),
                    phase_name=phase.name,
                    planned_end_date=phase.planned_end_date.isoformat(),
                    projected_end_date=projected_end.isoformat(),
                    completion_rate=round(completion_rate, 3),
                    deviation_days=deviation_days,
                    is_delayed=deviation_days > 0,
                )
            )

        overall_rate = (
            sum(p.completion_rate for p in phase_items) / len(phase_items) if phase_items else 0.0
        )

        ai_narrative = await self._generate_phase_narrative(project.name, target_date, phase_items)

        return PhaseProgressResult(
            project_id=project_id,
            as_of_date=target_date.isoformat(),
            phases=phase_items,
            overall_completion_rate=round(overall_rate, 3),
            ai_narrative=ai_narrative,
        )

    # ------------------------------------------------------------------
    # LLM ナラティブ生成
    # ------------------------------------------------------------------

    async def _generate_daily_narrative(
        self,
        project_name: str,
        target_date: date,
        task_summary: TaskSummary,
        report_summary: ReportSummary,
        alert_summary: list[AlertSummaryItem],
    ) -> str:
        """日次サマリの AI ナラティブを生成する。"""
        alert_lines = (
            "\n".join(f"  - {a.category} ({a.severity}): {a.count}件" for a in alert_summary)
            or "  （なし）"
        )

        prompt = (
            f"プロジェクト「{project_name}」の{target_date}日次サマリを日本語 3〜5 文で生成してください。\n\n"
            f"タスク状況:\n"
            f"  全体: {task_summary.total}件\n"
            f"  進行中: {task_summary.in_progress}件\n"
            f"  完了: {task_summary.done}件\n"
            f"  ブロック中: {task_summary.blocked}件\n"
            f"  期日超過: {task_summary.overdue}件\n\n"
            f"日報提出状況: {report_summary.submitted}/{report_summary.total_members}件\n\n"
            f"アクティブアラート:\n{alert_lines}\n\n"
            "日次サマリ:"
        )
        try:
            response = await self._llm.generate(prompt, max_tokens=400, temperature=0.3)
            return response.content.strip()
        except Exception as exc:
            logger.warning("LLM 日次ナラティブ生成失敗: %s", exc)
            return (
                f"{target_date}時点のサマリ。"
                f"タスク総数{task_summary.total}件（完了{task_summary.done}件、期日超過{task_summary.overdue}件）。"
                f"日報提出率: {report_summary.submitted}/{report_summary.total_members}件。"
            )

    async def _generate_phase_narrative(
        self,
        project_name: str,
        target_date: date,
        phases: list[PhaseProgressItem],
    ) -> str:
        """フェーズ進捗の AI ナラティブを生成する。"""
        phase_lines = (
            "\n".join(
                f"  - {p.phase_name}: 進捗{p.completion_rate:.0%}、"
                f"{'遅延 ' + str(p.deviation_days) + '日' if p.is_delayed else '計画通り'}"
                for p in phases
            )
            or "  （フェーズ未設定）"
        )

        prompt = (
            f"プロジェクト「{project_name}」の{target_date}時点のフェーズ進捗を"
            f"日本語 2〜4 文でサマリしてください。\n\n"
            f"フェーズ進捗:\n{phase_lines}\n\n"
            "フェーズ進捗サマリ:"
        )
        try:
            response = await self._llm.generate(prompt, max_tokens=300, temperature=0.3)
            return response.content.strip()
        except Exception as exc:
            logger.warning("LLM フェーズナラティブ生成失敗: %s", exc)
            delayed = [p for p in phases if p.is_delayed]
            if delayed:
                return f"{len(delayed)}フェーズが遅延しています: {', '.join(p.phase_name for p in delayed)}"
            return "すべてのフェーズは計画通りに進行中です。"

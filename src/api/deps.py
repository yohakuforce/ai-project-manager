"""
FastAPI 依存性注入（DI）コンテナ。

settings.use_database=False（既定）でインメモリ、True で SqlAlchemy 実装に切替。
Notifier / AuditLogRepository / LLMAdapter はファクトリ経由でシングルトン保持。
"""

from __future__ import annotations

from src.application.alert.service import AlertService
from src.application.assign.service import AssignService
from src.application.gate.service import GateService
from src.application.overview.service import OverviewService
from src.application.plan.service import PlanService
from src.application.standup.service import StandupService
from src.application.status.service import ProjectStatusService
from src.application.track.service import TrackService
from src.application.wrapup.service import WrapUpService
from src.config.settings import get_settings
from src.domain.alert.repository import AlertRepository
from src.domain.audit.repository import AuditLogRepository
from src.domain.gate.repository import LeaderGateRepository
from src.domain.member.repository import MemberRepository
from src.domain.project.repository import ProjectRepository
from src.domain.reporting.repository import DailyReportRepository
from src.infrastructure.audit.factory import build_audit_log_repository
from src.infrastructure.context_hub.factory import create_context_hub_client
from src.infrastructure.llm.adapter import LLMAdapter
from src.infrastructure.llm.auditing_adapter import AuditingLLMAdapter
from src.infrastructure.llm.factory import create_llm_adapter
from src.infrastructure.notifiers.factory import build_notifier
from src.infrastructure.notifiers.protocol import Notifier
from src.infrastructure.repositories.factory import RepositoryBundle, build_repositories

_llm_adapter: LLMAdapter | None = None


def get_llm_adapter() -> LLMAdapter:
    """LLM アダプタ（設定から生成、AuditingLLMAdapter でラップ）。

    シングルトンで保持する。AuditLogRepository が利用可能なら
    LLM_CALL イベントを自動記録する。
    """
    global _llm_adapter
    if _llm_adapter is not None:
        return _llm_adapter
    base = create_llm_adapter()
    _llm_adapter = AuditingLLMAdapter(inner=base, audit_repository=get_audit_repo())
    return _llm_adapter


def get_context_hub_client():
    """Context-Hub クライアント（モック or 実 HTTP）。"""
    return create_context_hub_client()


_notifier: Notifier | None = None
_audit_repo: AuditLogRepository | None = None


def get_notifier() -> Notifier:
    """Notifier シングルトン（settings 由来）。"""
    global _notifier
    if _notifier is None:
        _notifier = build_notifier(get_settings())
    return _notifier


def get_audit_repo() -> AuditLogRepository:
    """AuditLogRepository シングルトン（settings 由来）。"""
    global _audit_repo
    if _audit_repo is None:
        _audit_repo = build_audit_log_repository(get_settings())
    return _audit_repo


# --- リポジトリ束（settings.use_database で SqlAlchemy / InMemory を切替） ---
_repositories: RepositoryBundle | None = None


def get_repositories() -> RepositoryBundle:
    """RepositoryBundle シングルトン。"""
    global _repositories
    if _repositories is None:
        _repositories = build_repositories(get_settings())
    return _repositories


def get_project_repo() -> ProjectRepository:
    return get_repositories().project


def get_member_repo() -> MemberRepository:
    return get_repositories().member


def get_alert_repo() -> AlertRepository:
    return get_repositories().alert


def get_report_repo() -> DailyReportRepository:
    return get_repositories().report


def get_gate_repo() -> LeaderGateRepository:
    return get_repositories().gate


def reset_singletons_for_tests() -> None:
    """テスト用: 全シングルトンをリセットする。production では呼ばない。"""
    global _llm_adapter, _notifier, _audit_repo, _repositories
    _llm_adapter = None
    _notifier = None
    _audit_repo = None
    _repositories = None


# --- Application Services ---


def get_plan_service() -> PlanService:
    return PlanService(
        project_repository=get_project_repo(),
        context_hub_client=get_context_hub_client(),
        llm_adapter=get_llm_adapter(),
        audit_repository=get_audit_repo(),
    )


def get_assign_service() -> AssignService:
    return AssignService(
        project_repository=get_project_repo(),
        member_repository=get_member_repo(),
        llm_adapter=get_llm_adapter(),
        audit_repository=get_audit_repo(),
    )


def get_track_service() -> TrackService:
    settings = get_settings()
    return TrackService(
        project_repository=get_project_repo(),
        member_repository=get_member_repo(),
        daily_report_repository=get_report_repo(),
        llm_adapter=get_llm_adapter(),
        notifier=get_notifier(),
        default_channel=settings.slack_notification_channel,
        leader_channel=settings.slack_notification_channel,
        audit_repository=get_audit_repo(),
    )


def get_alert_service() -> AlertService:
    settings = get_settings()
    return AlertService(
        project_repository=get_project_repo(),
        member_repository=get_member_repo(),
        alert_repository=get_alert_repo(),
        daily_report_repository=get_report_repo(),
        llm_adapter=get_llm_adapter(),
        notifier=get_notifier(),
        alert_channel=settings.slack_notification_channel,
        audit_repository=get_audit_repo(),
    )


def get_overview_service() -> OverviewService:
    return OverviewService(
        project_repository=get_project_repo(),
        member_repository=get_member_repo(),
        alert_repository=get_alert_repo(),
        daily_report_repository=get_report_repo(),
        llm_adapter=get_llm_adapter(),
    )


def get_standup_service() -> StandupService:
    settings = get_settings()
    return StandupService(
        project_repository=get_project_repo(),
        member_repository=get_member_repo(),
        daily_report_repository=get_report_repo(),
        assign_service=get_assign_service(),
        llm_adapter=get_llm_adapter(),
        context_hub_client=get_context_hub_client(),
        notifier=get_notifier(),
        leader_channel=settings.slack_notification_channel,
    )


def get_wrap_up_service() -> WrapUpService:
    settings = get_settings()
    # open_gate 用の素の GateService（後続ハンドラは不要。解決は API 側の GateService が担う）。
    gate_service = GateService(get_gate_repo(), audit_repository=get_audit_repo())
    return WrapUpService(
        project_repository=get_project_repo(),
        member_repository=get_member_repo(),
        daily_report_repository=get_report_repo(),
        overview_service=get_overview_service(),
        gate_service=gate_service,
        notifier=get_notifier(),
        leader_channel=settings.slack_notification_channel,
    )


def get_status_service() -> ProjectStatusService:
    settings = get_settings()
    return ProjectStatusService(
        overview_service=get_overview_service(),
        assign_service=get_assign_service(),
        notifier=get_notifier(),
        leader_channel=settings.slack_notification_channel,
    )


def get_gate_service() -> GateService:
    """ゲート解決用 GateService。後続ハンドラ（総括継続 / final_analysis）を登録して返す。

    repositories は singleton のため、WrapUpService / ProjectStatusService を都度生成しても
    同一データストアを共有する。
    """
    gate_service = GateService(get_gate_repo(), audit_repository=get_audit_repo())
    wrap_up = get_wrap_up_service()
    status = get_status_service()
    gate_service.register_continuations(
        on_wrap_up_proceed=wrap_up.run_summary_and_open_gate,
        on_task_state_confirmed=status.run_final_analysis,
    )
    return gate_service

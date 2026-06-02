"""SQLAlchemy（非同期）リポジトリ実装。

session_factory を受け取り、メソッドごとに短命セッションを開く設計。
集約境界の永続化はトランザクション 1 つで完結させる（部分更新を避ける）。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.domain.alert.aggregate import Alert, AlertId, AlertSeverity, AlertStatus
from src.domain.alert.repository import AlertRepository
from src.domain.gate.aggregate import GateStatus, LeaderGate, LeaderGateId
from src.domain.gate.repository import LeaderGateRepository
from src.domain.member.aggregate import Member
from src.domain.member.repository import MemberRepository
from src.domain.member.value_objects import MemberId
from src.domain.project.aggregate import Project
from src.domain.project.repository import ProjectRepository
from src.domain.project.value_objects import ProjectId, ProjectStatus
from src.domain.reporting.aggregate import DailyReport
from src.domain.reporting.repository import DailyReportRepository
from src.domain.reporting.value_objects import DailyReportId, ReportStatus
from src.infrastructure.db.models import (
    AlertModel,
    AssignmentModel,
    DailyReportModel,
    LeaderGateModel,
    MemberModel,
    ProjectModel,
    TaskModel,
)
from src.infrastructure.repositories.mappers import (
    alert_from_model,
    alert_to_model,
    assignment_to_model,
    daily_report_from_model,
    daily_report_to_model,
    leader_gate_from_model,
    leader_gate_to_model,
    member_from_model,
    member_to_model,
    project_from_model,
    project_to_model,
    task_to_model,
)


class SqlAlchemyProjectRepository(ProjectRepository):
    """Project 集約の PostgreSQL 永続化。

    集約境界:
      - Project は子の Task / Assignment を所有する。
      - save() は upsert で集約全体を置き換える（部分更新は許さない）。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_by_id(self, project_id: ProjectId) -> Project | None:
        async with self._session_factory() as session:
            model = await self._fetch_with_children(session, str(project_id))
            return project_from_model(model) if model else None

    async def find_all_active(self) -> list[Project]:
        return await self._find_by_status_value(ProjectStatus.ACTIVE.value)

    async def find_by_status(self, status: ProjectStatus) -> list[Project]:
        return await self._find_by_status_value(status.value)

    async def save(self, project: Project) -> Project:
        async with self._session_factory() as session:
            await self._upsert(session, project)
            await session.commit()
        return project

    async def _find_by_status_value(self, status_value: str) -> list[Project]:
        async with self._session_factory() as session:
            stmt = (
                select(ProjectModel)
                .where(ProjectModel.status == status_value)
                .options(
                    selectinload(ProjectModel.tasks),
                    selectinload(ProjectModel.assignments),
                )
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [project_from_model(r) for r in rows]

    async def _fetch_with_children(
        self, session: AsyncSession, project_id: str
    ) -> ProjectModel | None:
        stmt = (
            select(ProjectModel)
            .where(ProjectModel.id == project_id)
            .options(
                selectinload(ProjectModel.tasks),
                selectinload(ProjectModel.assignments),
            )
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _upsert(self, session: AsyncSession, project: Project) -> None:
        project_id = str(project.project_id)
        existing = await self._fetch_with_children(session, project_id)

        if existing is None:
            session.add(project_to_model(project))
            return

        # スカラ列の差分更新
        existing.name = project.name
        existing.customer = project.customer
        existing.goal = project.goal
        existing.context_hub_project_id = project.context_hub_ref.context_hub_project_id
        existing.context_hub_api_endpoint = project.context_hub_ref.api_endpoint
        existing.status = project.status.value
        existing.updated_at = project.updated_at

        # JSON 列（Phase / IssueStatusMapping）はマッパー経由で全置換
        new_orm = project_to_model(project)
        existing.phases_json = new_orm.phases_json
        existing.issue_status_mappings_json = new_orm.issue_status_mappings_json

        # 子コレクションは全削除→再追加（小規模集約のため）
        await session.execute(delete(TaskModel).where(TaskModel.project_id == project_id))
        await session.execute(
            delete(AssignmentModel).where(AssignmentModel.project_id == project_id)
        )
        await session.flush()

        for task in project.tasks:
            session.add(task_to_model(task, project_id))
        for assignment in project.assignments:
            session.add(assignment_to_model(assignment, project_id))


class SqlAlchemyMemberRepository(MemberRepository):
    """Member 集約の PostgreSQL 永続化。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_by_id(self, member_id: MemberId) -> Member | None:
        async with self._session_factory() as session:
            stmt = select(MemberModel).where(MemberModel.id == str(member_id))
            model = (await session.execute(stmt)).scalar_one_or_none()
            return member_from_model(model) if model else None

    async def find_by_external_id(self, external_id: str) -> Member | None:
        async with self._session_factory() as session:
            stmt = select(MemberModel).where(MemberModel.external_id == external_id)
            model = (await session.execute(stmt)).scalar_one_or_none()
            return member_from_model(model) if model else None

    async def find_all(self) -> list[Member]:
        async with self._session_factory() as session:
            rows = (await session.execute(select(MemberModel))).scalars().all()
            return [member_from_model(r) for r in rows]

    async def save(self, member: Member) -> Member:
        async with self._session_factory() as session:
            member_id = str(member.member_id)
            existing = (
                await session.execute(select(MemberModel).where(MemberModel.id == member_id))
            ).scalar_one_or_none()

            new_orm = member_to_model(member)
            if existing is None:
                session.add(new_orm)
            else:
                existing.external_id = new_orm.external_id
                existing.name = new_orm.name
                existing.role = new_orm.role
                existing.skills_json = new_orm.skills_json
                existing.availability_json = new_orm.availability_json
                existing.performance_history_json = new_orm.performance_history_json
                existing.updated_at = new_orm.updated_at
            await session.commit()
        return member


class SqlAlchemyAlertRepository(AlertRepository):
    """Alert 集約の PostgreSQL 永続化。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_by_id(self, alert_id: AlertId) -> Alert | None:
        async with self._session_factory() as session:
            stmt = select(AlertModel).where(AlertModel.id == str(alert_id))
            model = (await session.execute(stmt)).scalar_one_or_none()
            return alert_from_model(model) if model else None

    async def find_active_by_project(self, project_id: str) -> list[Alert]:
        return await self._query(project_id, status_value=AlertStatus.ACTIVE.value)

    async def find_by_severity(self, project_id: str, severity: AlertSeverity) -> list[Alert]:
        async with self._session_factory() as session:
            stmt = select(AlertModel).where(
                AlertModel.project_id == project_id,
                AlertModel.severity == severity.value,
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [alert_from_model(r) for r in rows]

    async def find_by_status(self, project_id: str, status: AlertStatus) -> list[Alert]:
        return await self._query(project_id, status_value=status.value)

    async def save(self, alert: Alert) -> Alert:
        async with self._session_factory() as session:
            alert_id = str(alert.alert_id)
            existing = (
                await session.execute(select(AlertModel).where(AlertModel.id == alert_id))
            ).scalar_one_or_none()
            new_orm = alert_to_model(alert)
            if existing is None:
                session.add(new_orm)
            else:
                existing.project_id = new_orm.project_id
                existing.category = new_orm.category
                existing.severity = new_orm.severity
                existing.ai_generated_message = new_orm.ai_generated_message
                existing.evidence_json = new_orm.evidence_json
                existing.target_task_id = new_orm.target_task_id
                existing.target_member_id = new_orm.target_member_id
                existing.status = new_orm.status
                existing.acknowledged_by = new_orm.acknowledged_by
                existing.acknowledged_at = new_orm.acknowledged_at
                existing.resolved_at = new_orm.resolved_at
            await session.commit()
        return alert

    async def _query(self, project_id: str, *, status_value: str) -> list[Alert]:
        async with self._session_factory() as session:
            stmt = select(AlertModel).where(
                AlertModel.project_id == project_id,
                AlertModel.status == status_value,
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [alert_from_model(r) for r in rows]


class SqlAlchemyDailyReportRepository(DailyReportRepository):
    """DailyReport 集約の PostgreSQL 永続化。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_by_id(self, report_id: DailyReportId) -> DailyReport | None:
        async with self._session_factory() as session:
            stmt = select(DailyReportModel).where(DailyReportModel.id == str(report_id))
            model = (await session.execute(stmt)).scalar_one_or_none()
            return daily_report_from_model(model) if model else None

    async def find_by_member_and_date(
        self, member_id: str, report_date: date
    ) -> DailyReport | None:
        async with self._session_factory() as session:
            stmt = select(DailyReportModel).where(
                DailyReportModel.member_id == member_id,
                DailyReportModel.report_date == report_date,
            )
            model = (await session.execute(stmt)).scalar_one_or_none()
            return daily_report_from_model(model) if model else None

    async def find_by_project_and_date(
        self, project_id: str, report_date: date
    ) -> list[DailyReport]:
        async with self._session_factory() as session:
            stmt = select(DailyReportModel).where(
                DailyReportModel.project_id == project_id,
                DailyReportModel.report_date == report_date,
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [daily_report_from_model(r) for r in rows]

    async def find_by_status(self, project_id: str, status: ReportStatus) -> list[DailyReport]:
        async with self._session_factory() as session:
            stmt = select(DailyReportModel).where(
                DailyReportModel.project_id == project_id,
                DailyReportModel.status == status.value,
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [daily_report_from_model(r) for r in rows]

    async def save(self, report: DailyReport) -> DailyReport:
        async with self._session_factory() as session:
            report_id = str(report.report_id)
            existing = (
                await session.execute(
                    select(DailyReportModel).where(DailyReportModel.id == report_id)
                )
            ).scalar_one_or_none()
            new_orm = daily_report_to_model(report)
            if existing is None:
                session.add(new_orm)
            else:
                existing.member_id = new_orm.member_id
                existing.project_id = new_orm.project_id
                existing.report_date = new_orm.report_date
                existing.template_json = new_orm.template_json
                existing.responses_json = new_orm.responses_json
                existing.ai_summary = new_orm.ai_summary
                existing.status = new_orm.status
                existing.delivered_at = new_orm.delivered_at
                existing.submitted_at = new_orm.submitted_at
                existing.analyzed_at = new_orm.analyzed_at
            await session.commit()
        return report


class SqlAlchemyLeaderGateRepository(LeaderGateRepository):
    """LeaderGate（リーダー確認ゲート）の PostgreSQL 永続化。

    確認が翌日になっても保持されるよう DB に永続化する。プロセス再起動を跨いで
    PENDING ゲートが残り、リーダーは翌日でも解決できる。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_by_id(self, gate_id: LeaderGateId) -> LeaderGate | None:
        async with self._session_factory() as session:
            stmt = select(LeaderGateModel).where(LeaderGateModel.id == str(gate_id))
            model = (await session.execute(stmt)).scalar_one_or_none()
            return leader_gate_from_model(model) if model else None

    async def find_pending_by_project(self, project_id: str) -> list[LeaderGate]:
        return await self._query(project_id, status_value=GateStatus.PENDING.value)

    async def find_by_status(self, project_id: str, status: GateStatus) -> list[LeaderGate]:
        return await self._query(project_id, status_value=status.value)

    async def save(self, gate: LeaderGate) -> LeaderGate:
        async with self._session_factory() as session:
            gate_id = str(gate.gate_id)
            existing = (
                await session.execute(select(LeaderGateModel).where(LeaderGateModel.id == gate_id))
            ).scalar_one_or_none()
            new_orm = leader_gate_to_model(gate)
            if existing is None:
                session.add(new_orm)
            else:
                existing.project_id = new_orm.project_id
                existing.gate_type = new_orm.gate_type
                existing.gate_date = new_orm.gate_date
                existing.status = new_orm.status
                existing.context_json = new_orm.context_json
                existing.decision = new_orm.decision
                existing.resolved_by = new_orm.resolved_by
                existing.resolved_at = new_orm.resolved_at
            await session.commit()
        return gate

    async def _query(self, project_id: str, *, status_value: str) -> list[LeaderGate]:
        async with self._session_factory() as session:
            stmt = select(LeaderGateModel).where(
                LeaderGateModel.project_id == project_id,
                LeaderGateModel.status == status_value,
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [leader_gate_from_model(r) for r in rows]

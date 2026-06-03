"""
インメモリリポジトリ実装。
テスト・開発・PoC 環境用。プロセス再起動でデータは消える。
本番は PostgreSQL + SQLAlchemy 実装に差し替える。
"""

from __future__ import annotations

from datetime import date

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


class InMemoryProjectRepository(ProjectRepository):
    def __init__(self) -> None:
        self._store: dict[str, Project] = {}

    async def find_by_id(self, project_id: ProjectId) -> Project | None:
        return self._store.get(str(project_id))

    async def find_all_active(self) -> list[Project]:
        return [p for p in self._store.values() if p.status == ProjectStatus.ACTIVE]

    async def find_by_status(self, status: ProjectStatus) -> list[Project]:
        return [p for p in self._store.values() if p.status == status]

    async def save(self, project: Project) -> Project:
        self._store[str(project.project_id)] = project
        return project

    async def delete(self, project_id: ProjectId) -> None:
        self._store.pop(str(project_id), None)

    def clear(self) -> None:
        """テスト用: データをリセットする。"""
        self._store.clear()


class InMemoryMemberRepository(MemberRepository):
    def __init__(self) -> None:
        self._store: dict[str, Member] = {}

    async def find_by_id(self, member_id: MemberId) -> Member | None:
        return self._store.get(str(member_id))

    async def find_by_external_id(self, external_id: str) -> Member | None:
        return next(
            (m for m in self._store.values() if m.external_id == external_id),
            None,
        )

    async def find_all(self) -> list[Member]:
        return list(self._store.values())

    async def save(self, member: Member) -> Member:
        self._store[str(member.member_id)] = member
        return member

    async def delete(self, member_id: MemberId) -> None:
        self._store.pop(str(member_id), None)

    def clear(self) -> None:
        self._store.clear()


class InMemoryAlertRepository(AlertRepository):
    def __init__(self) -> None:
        self._store: dict[str, Alert] = {}

    async def find_by_id(self, alert_id: AlertId) -> Alert | None:
        return self._store.get(str(alert_id))

    async def find_active_by_project(self, project_id: str) -> list[Alert]:
        return [
            a
            for a in self._store.values()
            if a.project_id == project_id and a.status == AlertStatus.ACTIVE
        ]

    async def find_by_severity(self, project_id: str, severity: AlertSeverity) -> list[Alert]:
        return [
            a for a in self._store.values() if a.project_id == project_id and a.severity == severity
        ]

    async def find_by_status(self, project_id: str, status: AlertStatus) -> list[Alert]:
        return [
            a for a in self._store.values() if a.project_id == project_id and a.status == status
        ]

    async def save(self, alert: Alert) -> Alert:
        self._store[str(alert.alert_id)] = alert
        return alert

    def clear(self) -> None:
        self._store.clear()


class InMemoryDailyReportRepository(DailyReportRepository):
    def __init__(self) -> None:
        self._store: dict[str, DailyReport] = {}

    async def find_by_id(self, report_id: DailyReportId) -> DailyReport | None:
        return self._store.get(str(report_id))

    async def find_by_member_and_date(
        self, member_id: str, report_date: date
    ) -> DailyReport | None:
        return next(
            (
                r
                for r in self._store.values()
                if r.member_id == member_id and r.report_date == report_date
            ),
            None,
        )

    async def find_by_project_and_date(
        self, project_id: str, report_date: date
    ) -> list[DailyReport]:
        return [
            r
            for r in self._store.values()
            if r.project_id == project_id and r.report_date == report_date
        ]

    async def find_by_status(self, project_id: str, status: ReportStatus) -> list[DailyReport]:
        return [
            r for r in self._store.values() if r.project_id == project_id and r.status == status
        ]

    async def save(self, report: DailyReport) -> DailyReport:
        self._store[str(report.report_id)] = report
        return report

    def clear(self) -> None:
        self._store.clear()


class InMemoryLeaderGateRepository(LeaderGateRepository):
    """リーダー確認ゲートのインメモリ実装。

    MVP ではこの実装のみ。プロセス再起動で未解決ゲートは消える（手動再実行で復旧）。
    """

    def __init__(self) -> None:
        self._store: dict[str, LeaderGate] = {}

    async def find_by_id(self, gate_id: LeaderGateId) -> LeaderGate | None:
        return self._store.get(str(gate_id))

    async def find_pending_by_project(self, project_id: str) -> list[LeaderGate]:
        return [
            g
            for g in self._store.values()
            if g.project_id == project_id and g.status == GateStatus.PENDING
        ]

    async def find_by_status(self, project_id: str, status: GateStatus) -> list[LeaderGate]:
        return [
            g for g in self._store.values() if g.project_id == project_id and g.status == status
        ]

    async def save(self, gate: LeaderGate) -> LeaderGate:
        self._store[str(gate.gate_id)] = gate
        return gate

    def clear(self) -> None:
        self._store.clear()

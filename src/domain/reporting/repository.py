"""DailyReport 集約のリポジトリインターフェース。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from .aggregate import DailyReport
from .value_objects import DailyReportId, ReportStatus


class DailyReportRepository(ABC):
    @abstractmethod
    async def find_by_id(self, report_id: DailyReportId) -> DailyReport | None: ...

    @abstractmethod
    async def find_by_member_and_date(
        self, member_id: str, report_date: date
    ) -> DailyReport | None: ...

    @abstractmethod
    async def find_by_project_and_date(
        self, project_id: str, report_date: date
    ) -> list[DailyReport]: ...

    @abstractmethod
    async def find_by_status(self, project_id: str, status: ReportStatus) -> list[DailyReport]: ...

    @abstractmethod
    async def save(self, report: DailyReport) -> DailyReport: ...

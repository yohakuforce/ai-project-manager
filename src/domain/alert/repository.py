"""Alert 集約のリポジトリインターフェース。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .aggregate import Alert, AlertId, AlertSeverity, AlertStatus


class AlertRepository(ABC):
    @abstractmethod
    async def find_by_id(self, alert_id: AlertId) -> Alert | None: ...

    @abstractmethod
    async def find_active_by_project(self, project_id: str) -> list[Alert]: ...

    @abstractmethod
    async def find_by_severity(self, project_id: str, severity: AlertSeverity) -> list[Alert]: ...

    @abstractmethod
    async def find_by_status(self, project_id: str, status: AlertStatus) -> list[Alert]: ...

    @abstractmethod
    async def save(self, alert: Alert) -> Alert: ...

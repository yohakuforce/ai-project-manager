"""インメモリ AuditLogRepository（テスト用）。"""

from __future__ import annotations

from src.domain.audit.aggregate import AuditLog
from src.domain.audit.repository import AuditLogRepository


class InMemoryAuditLogRepository(AuditLogRepository):
    """append された AuditLog をメモリに保持する。production では使わない。"""

    def __init__(self) -> None:
        self._logs: list[AuditLog] = []

    async def append(self, log: AuditLog) -> None:
        self._logs.append(log)

    async def find_by_project(self, project_id: str, limit: int = 100) -> list[AuditLog]:
        results = [log for log in self._logs if log.project_id == project_id]
        return results[-limit:]

    async def find_by_actor(self, actor: str, limit: int = 100) -> list[AuditLog]:
        results = [log for log in self._logs if log.actor == actor]
        return results[-limit:]

    @property
    def all_logs(self) -> list[AuditLog]:
        return list(self._logs)

    def reset(self) -> None:
        self._logs.clear()

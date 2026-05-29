"""AuditLog のリポジトリインターフェース。追記のみ。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .aggregate import AuditLog


class AuditLogRepository(ABC):
    @abstractmethod
    async def append(self, log: AuditLog) -> None: ...  # 追記のみ。更新・削除禁止

    @abstractmethod
    async def find_by_project(self, project_id: str, limit: int = 100) -> list[AuditLog]: ...

    @abstractmethod
    async def find_by_actor(self, actor: str, limit: int = 100) -> list[AuditLog]: ...

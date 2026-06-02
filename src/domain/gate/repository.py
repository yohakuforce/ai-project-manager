"""LeaderGate 集約のリポジトリインターフェース。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .aggregate import GateStatus, LeaderGate, LeaderGateId


class LeaderGateRepository(ABC):
    @abstractmethod
    async def find_by_id(self, gate_id: LeaderGateId) -> LeaderGate | None: ...

    @abstractmethod
    async def find_pending_by_project(self, project_id: str) -> list[LeaderGate]: ...

    @abstractmethod
    async def find_by_status(self, project_id: str, status: GateStatus) -> list[LeaderGate]: ...

    @abstractmethod
    async def save(self, gate: LeaderGate) -> LeaderGate: ...

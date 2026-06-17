"""Member 集約のリポジトリインターフェース。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.project.value_objects import ProjectId

from .aggregate import Member
from .value_objects import MemberId


class MemberRepository(ABC):
    @abstractmethod
    async def find_by_id(self, member_id: MemberId) -> Member | None: ...

    @abstractmethod
    async def find_by_external_id(self, external_id: str) -> Member | None: ...

    @abstractmethod
    async def find_all(self) -> list[Member]: ...

    @abstractmethod
    async def find_by_project_id(self, project_id: ProjectId) -> list[Member]: ...

    @abstractmethod
    async def save(self, member: Member) -> Member: ...

    @abstractmethod
    async def delete(self, member_id: MemberId) -> None: ...


class ProjectMemberRepository(ABC):
    """Project ↔ Member メンバーシップ管理リポジトリインターフェース。"""

    @abstractmethod
    async def add(self, project_id: ProjectId, member_id: MemberId) -> None: ...

    @abstractmethod
    async def remove(self, project_id: ProjectId, member_id: MemberId) -> None: ...

    @abstractmethod
    async def list_member_ids(self, project_id: ProjectId) -> list[str]: ...

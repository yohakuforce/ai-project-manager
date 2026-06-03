"""Member 集約のリポジトリインターフェース。"""

from __future__ import annotations

from abc import ABC, abstractmethod

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
    async def save(self, member: Member) -> Member: ...

    @abstractmethod
    async def delete(self, member_id: MemberId) -> None: ...

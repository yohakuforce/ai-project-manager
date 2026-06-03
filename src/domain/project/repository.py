"""
Project 集約のリポジトリインターフェース。
インフラ層の実装クラス（PostgreSQL）はこのインターフェースを実装する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .aggregate import Project
from .value_objects import ProjectId, ProjectStatus


class ProjectRepository(ABC):
    @abstractmethod
    async def find_by_id(self, project_id: ProjectId) -> Project | None: ...

    @abstractmethod
    async def find_all_active(self) -> list[Project]: ...

    @abstractmethod
    async def find_by_status(self, status: ProjectStatus) -> list[Project]: ...

    @abstractmethod
    async def save(self, project: Project) -> Project: ...

    @abstractmethod
    async def delete(self, project_id: ProjectId) -> None: ...

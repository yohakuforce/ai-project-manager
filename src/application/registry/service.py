"""
RegistryService — プロジェクト・メンバーの登録と一覧を担う Application Service。

AI-PM はこれまで登録専用の口が無く seed スクリプト頼みだったため、GUI / API から
プロジェクトとメンバーを作成・一覧できるようにする薄いサービス。
保存先は注入されたリポジトリ（settings.use_database に従う）に委ねる。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.domain.member.aggregate import Member
from src.domain.member.repository import MemberRepository, ProjectMemberRepository
from src.domain.member.value_objects import MemberId, MemberRole
from src.domain.project.aggregate import Project
from src.domain.project.repository import ProjectRepository
from src.domain.project.value_objects import ContextHubProjectRef, ProjectId

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectView:
    project_id: str
    name: str
    customer: str
    goal: str
    context_hub_project_id: str
    status: str
    task_count: int


@dataclass(frozen=True)
class MemberView:
    member_id: str
    external_id: str
    name: str
    role: str


class RegistryError(Exception):
    """登録時の入力不正（重複・必須欠落など）。"""


def _project_view(p: Project) -> ProjectView:
    return ProjectView(
        project_id=str(p.project_id),
        name=p.name,
        customer=p.customer,
        goal=p.goal,
        context_hub_project_id=p.context_hub_ref.context_hub_project_id,
        status=p.status.value,
        task_count=len(p.tasks),
    )


def _member_view(m: Member) -> MemberView:
    return MemberView(
        member_id=str(m.member_id),
        external_id=m.external_id,
        name=m.name,
        role=m.role.value,
    )


class RegistryService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        member_repository: MemberRepository,
        project_member_repository: ProjectMemberRepository | None = None,
    ) -> None:
        self._project_repo = project_repository
        self._member_repo = member_repository
        self._project_member_repo = project_member_repository

    # --- プロジェクト ---

    async def list_projects(self) -> list[ProjectView]:
        projects = await self._project_repo.find_all_active()
        return [_project_view(p) for p in projects]

    async def create_project(
        self,
        *,
        name: str,
        customer: str,
        goal: str,
        context_hub_project_id: str,
        api_endpoint: str,
    ) -> ProjectView:
        if not name.strip():
            raise RegistryError("プロジェクト名は必須です。")
        project = Project(
            project_id=ProjectId.generate(),
            name=name.strip(),
            customer=customer.strip() or "（未設定）",
            goal=goal.strip() or "（未設定）",
            context_hub_ref=ContextHubProjectRef(
                context_hub_project_id=context_hub_project_id.strip() or "(none)",
                api_endpoint=api_endpoint.strip(),
            ),
        )
        await self._project_repo.save(project)
        logger.info("プロジェクト登録: id=%s name=%s", project.project_id, project.name)
        return _project_view(project)

    async def delete_project(self, project_id: str) -> str:
        """プロジェクトを削除し、削除した名前を返す。見つからなければ RegistryError。"""
        try:
            pid = ProjectId.from_str(project_id)
        except (ValueError, AttributeError) as exc:
            raise RegistryError("不正なプロジェクトID です。") from exc
        existing = await self._project_repo.find_by_id(pid)
        if existing is None:
            raise RegistryError("対象のプロジェクトが見つかりません（既に削除済みかも）。")
        name = existing.name
        await self._project_repo.delete(pid)
        logger.info("プロジェクト削除: id=%s name=%s", project_id, name)
        return name

    # --- メンバー ---

    async def list_members(self) -> list[MemberView]:
        members = await self._member_repo.find_all()
        return [_member_view(m) for m in members]

    async def create_member(
        self,
        *,
        external_id: str,
        name: str,
        role: str,
    ) -> MemberView:
        external_id = external_id.strip()
        name = name.strip()
        if not external_id:
            raise RegistryError("external_id は必須です（Slack のユーザーID等）。")
        if not name:
            raise RegistryError("名前は必須です。")
        try:
            member_role = MemberRole(role.strip().lower())
        except ValueError as exc:
            valid = ", ".join(r.value for r in MemberRole)
            raise RegistryError(f"role は次から選んでください: {valid}") from exc

        existing = await self._member_repo.find_by_external_id(external_id)
        if existing is not None:
            raise RegistryError(f"external_id '{external_id}' は既に登録済みです。")

        member = Member(
            member_id=MemberId.generate(),
            external_id=external_id,
            name=name,
            role=member_role,
        )
        await self._member_repo.save(member)
        logger.info("メンバー登録: id=%s name=%s", member.member_id, member.name)
        return _member_view(member)

    async def delete_member(self, member_id: str) -> str:
        """メンバーを削除し、削除した名前を返す。見つからなければ RegistryError。"""
        try:
            mid = MemberId.from_str(member_id)
        except (ValueError, AttributeError) as exc:
            raise RegistryError("不正なメンバーID です。") from exc
        existing = await self._member_repo.find_by_id(mid)
        if existing is None:
            raise RegistryError("対象のメンバーが見つかりません（既に削除済みかも）。")
        name = existing.name
        await self._member_repo.delete(mid)
        logger.info("メンバー削除: id=%s name=%s", member_id, name)
        return name

    # --- プロジェクトメンバーシップ ---

    def _require_project_member_repo(self) -> ProjectMemberRepository:
        if self._project_member_repo is None:
            raise RegistryError("ProjectMemberRepository が設定されていません。")
        return self._project_member_repo

    async def add_member_to_project(self, project_id: str, member_id: str) -> None:
        """プロジェクトにメンバーを追加する。プロジェクト/メンバー不存在は RegistryError。"""
        pm_repo = self._require_project_member_repo()
        try:
            pid = ProjectId.from_str(project_id)
        except (ValueError, AttributeError) as exc:
            raise RegistryError("不正なプロジェクトID です。") from exc
        try:
            mid = MemberId.from_str(member_id)
        except (ValueError, AttributeError) as exc:
            raise RegistryError("不正なメンバーID です。") from exc
        if await self._project_repo.find_by_id(pid) is None:
            raise RegistryError("対象のプロジェクトが見つかりません。")
        if await self._member_repo.find_by_id(mid) is None:
            raise RegistryError("対象のメンバーが見つかりません。")
        await pm_repo.add(pid, mid)
        logger.info("プロジェクトメンバー追加: project=%s member=%s", project_id, member_id)

    async def remove_member_from_project(self, project_id: str, member_id: str) -> None:
        """プロジェクトからメンバーを除外する。"""
        pm_repo = self._require_project_member_repo()
        try:
            pid = ProjectId.from_str(project_id)
        except (ValueError, AttributeError) as exc:
            raise RegistryError("不正なプロジェクトID です。") from exc
        try:
            mid = MemberId.from_str(member_id)
        except (ValueError, AttributeError) as exc:
            raise RegistryError("不正なメンバーID です。") from exc
        await pm_repo.remove(pid, mid)
        logger.info("プロジェクトメンバー削除: project=%s member=%s", project_id, member_id)

    async def list_project_members(self, project_id: str) -> list[MemberView]:
        """プロジェクトに所属するメンバー一覧を返す。"""
        try:
            pid = ProjectId.from_str(project_id)
        except (ValueError, AttributeError) as exc:
            raise RegistryError("不正なプロジェクトID です。") from exc
        if await self._project_repo.find_by_id(pid) is None:
            raise RegistryError("対象のプロジェクトが見つかりません。")
        members = await self._member_repo.find_by_project_id(pid)
        return [_member_view(m) for m in members]

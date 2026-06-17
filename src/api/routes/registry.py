"""FastAPI ルーター — Registry（プロジェクトメンバー管理）エンドポイント。

/api/v1/registry/projects/{project_id}/members の CRUD。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.deps import get_registry_service
from src.api.middleware import verify_api_key
from src.application.registry.service import MemberView, RegistryError, RegistryService

router = APIRouter(prefix="/registry", tags=["registry"])


class AddMemberRequest(BaseModel):
    member_id: str = Field(..., description="AI-PM 内部のメンバー UUID")


class MemberListResponse(BaseModel):
    project_id: str
    members: list[MemberView]


@router.get(
    "/projects/{project_id}/members",
    response_model=MemberListResponse,
    summary="プロジェクトのメンバー一覧を取得",
    dependencies=[Depends(verify_api_key)],
)
async def list_project_members(
    project_id: str,
    service: RegistryService = Depends(get_registry_service),
) -> MemberListResponse:
    try:
        members = await service.list_project_members(project_id)
    except RegistryError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return MemberListResponse(project_id=project_id, members=members)


@router.post(
    "/projects/{project_id}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=MemberListResponse,
    summary="プロジェクトにメンバーを追加",
    dependencies=[Depends(verify_api_key)],
)
async def add_member_to_project(
    project_id: str,
    body: AddMemberRequest,
    service: RegistryService = Depends(get_registry_service),
) -> MemberListResponse:
    try:
        await service.add_member_to_project(project_id, body.member_id)
        members = await service.list_project_members(project_id)
    except RegistryError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return MemberListResponse(project_id=project_id, members=members)


@router.delete(
    "/projects/{project_id}/members/{member_id}",
    status_code=status.HTTP_200_OK,
    response_model=MemberListResponse,
    summary="プロジェクトからメンバーを削除",
    dependencies=[Depends(verify_api_key)],
)
async def remove_member_from_project(
    project_id: str,
    member_id: str,
    service: RegistryService = Depends(get_registry_service),
) -> MemberListResponse:
    try:
        await service.remove_member_from_project(project_id, member_id)
        members = await service.list_project_members(project_id)
    except RegistryError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return MemberListResponse(project_id=project_id, members=members)

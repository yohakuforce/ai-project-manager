"""FastAPI ルーター — Assign（割当案生成）エンドポイント。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.deps import get_assign_service
from src.api.middleware import verify_api_key
from src.application.assign.service import (
    AssignDraftResult,
    AssignmentDecisionResult,
    AssignService,
)

router = APIRouter(prefix="/assign", tags=["assign"])


class GenerateDraftsRequest(BaseModel):
    project_id: str = Field(..., description="AI-PM 内部のプロジェクト UUID")
    target_date: date | None = Field(default=None, description="稼働確認の基準日（省略時は今日）")


class AssignmentDecisionRequest(BaseModel):
    project_id: str
    assignment_id: str
    decided_by: str = Field(..., description="承認/却下したユーザー ID")


@router.post(
    "/generate-drafts",
    response_model=AssignDraftResult,
    summary="未割当タスクへの割当案を AI が生成",
    dependencies=[Depends(verify_api_key)],
)
async def generate_assignment_drafts(
    body: GenerateDraftsRequest,
    service: AssignService = Depends(get_assign_service),
) -> AssignDraftResult:
    """
    未割当タスクに対して AI が DRAFT 割当案を生成する。
    PL/PM が /confirm または /reject で確定・却下する。
    """
    try:
        return await service.generate_drafts(
            project_id=body.project_id,
            target_date=body.target_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/confirm",
    response_model=AssignmentDecisionResult,
    summary="割当案を承認",
    dependencies=[Depends(verify_api_key)],
)
async def confirm_assignment(
    body: AssignmentDecisionRequest,
    service: AssignService = Depends(get_assign_service),
) -> AssignmentDecisionResult:
    try:
        return await service.confirm_assignment(
            project_id=body.project_id,
            assignment_id=body.assignment_id,
            confirmed_by=body.decided_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/reject",
    response_model=AssignmentDecisionResult,
    summary="割当案を却下",
    dependencies=[Depends(verify_api_key)],
)
async def reject_assignment(
    body: AssignmentDecisionRequest,
    service: AssignService = Depends(get_assign_service),
) -> AssignmentDecisionResult:
    try:
        return await service.reject_assignment(
            project_id=body.project_id,
            assignment_id=body.assignment_id,
            rejected_by=body.decided_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

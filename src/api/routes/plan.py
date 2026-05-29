"""FastAPI ルーター — Plan（タスク抽出）エンドポイント。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.deps import get_plan_service
from src.api.middleware import verify_api_key
from src.application.plan.service import (
    ExtractTasksFromMeetingResult,
    ImportTasksFromIssuesResult,
    PlanService,
)

router = APIRouter(prefix="/plan", tags=["plan"])


class ExtractFromMeetingRequest(BaseModel):
    project_id: str = Field(..., description="AI-PM 内部のプロジェクト UUID")
    meeting_id: str = Field(..., description="Context-Hub の会議 ID")


class ImportFromIssuesRequest(BaseModel):
    project_id: str = Field(..., description="AI-PM 内部のプロジェクト UUID")
    source: str = Field(..., description="Issue ソース (backlog | redmine)")
    status_filter: str = Field(default="open", description="取得するステータス")
    updated_since: str | None = Field(default=None, description="ISO 8601 日時。差分取得時に指定")


@router.post(
    "/extract-from-meeting",
    response_model=ExtractTasksFromMeetingResult,
    summary="会議メモから Task を抽出",
    dependencies=[Depends(verify_api_key)],
)
async def extract_tasks_from_meeting(
    body: ExtractFromMeetingRequest,
    service: PlanService = Depends(get_plan_service),
) -> ExtractTasksFromMeetingResult:
    """
    Context-Hub の会議データから Task を抽出して Project に追加する。
    """
    try:
        return await service.extract_tasks_from_meeting(
            project_id=body.project_id,
            meeting_id=body.meeting_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/import-from-issues",
    response_model=ImportTasksFromIssuesResult,
    summary="Issue から Task をインポート",
    dependencies=[Depends(verify_api_key)],
)
async def import_tasks_from_issues(
    body: ImportFromIssuesRequest,
    service: PlanService = Depends(get_plan_service),
) -> ImportTasksFromIssuesResult:
    """
    Context-Hub の Issue を取り込んで Task を生成・追加する。
    """
    try:
        return await service.import_tasks_from_issues(
            project_id=body.project_id,
            source=body.source,
            status_filter=body.status_filter,
            updated_since=body.updated_since,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

"""FastAPI ルーター — Overview（俯瞰レポート）エンドポイント。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.deps import get_overview_service
from src.api.middleware import verify_api_key
from src.application.overview.service import (
    DailySummaryResult,
    OverviewService,
    PhaseProgressResult,
)

router = APIRouter(prefix="/overview", tags=["overview"])


class DailySummaryRequest(BaseModel):
    project_id: str
    summary_date: date | None = Field(default=None, description="対象日（省略時は今日）")


class PhaseProgressRequest(BaseModel):
    project_id: str
    as_of_date: date | None = Field(default=None, description="基準日（省略時は今日）")


@router.post(
    "/daily-summary",
    response_model=DailySummaryResult,
    summary="日次サマリを生成",
    dependencies=[Depends(verify_api_key)],
)
async def get_daily_summary(
    body: DailySummaryRequest,
    service: OverviewService = Depends(get_overview_service),
) -> DailySummaryResult:
    """
    タスク状態・日報提出状況・アクティブアラートを集約した日次サマリを生成する。
    通常はスケジューラが毎朝 7 時に自動生成する。
    """
    try:
        return await service.generate_daily_summary(
            project_id=body.project_id,
            summary_date=body.summary_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/phase-progress",
    response_model=PhaseProgressResult,
    summary="フェーズ進捗レポートを生成",
    dependencies=[Depends(verify_api_key)],
)
async def get_phase_progress(
    body: PhaseProgressRequest,
    service: OverviewService = Depends(get_overview_service),
) -> PhaseProgressResult:
    """
    各フェーズの計画対実績（PhaseProgress）を計算してレポートを生成する。
    """
    try:
        return await service.generate_phase_progress(
            project_id=body.project_id,
            as_of_date=body.as_of_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

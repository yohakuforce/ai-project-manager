"""FastAPI ルーター — Track（日報）エンドポイント。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.deps import get_track_service
from src.api.middleware import verify_api_key
from src.application.track.service import (
    AnalyzeResult,
    DeliverReportsResult,
    GenerateTemplatesResult,
    ResponseInput,
    SubmitResponsesResult,
    TrackService,
)

router = APIRouter(prefix="/track", tags=["track"])


class GenerateTemplatesRequest(BaseModel):
    project_id: str
    report_date: date | None = Field(default=None, description="対象日（省略時は今日）")


class DeliverRequest(BaseModel):
    project_id: str
    report_date: date | None = None


class ResponseInputSchema(BaseModel):
    question_id: str
    response_text: str


class SubmitResponsesRequest(BaseModel):
    report_id: str
    responses: list[ResponseInputSchema]
    finalize: bool = Field(default=True, description="True で SUBMITTED ステータスに変更")


class AnalyzeRequest(BaseModel):
    report_id: str


@router.post(
    "/generate-templates",
    response_model=GenerateTemplatesResult,
    summary="日報テンプレートを生成",
    dependencies=[Depends(verify_api_key)],
)
async def generate_templates(
    body: GenerateTemplatesRequest,
    service: TrackService = Depends(get_track_service),
) -> GenerateTemplatesResult:
    """
    プロジェクトの全メンバーに対して日報テンプレートを生成する。
    通常はスケジューラが毎朝 8 時に自動呼び出しする。
    """
    try:
        return await service.generate_daily_report_templates(
            project_id=body.project_id,
            report_date=body.report_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/deliver",
    response_model=DeliverReportsResult,
    summary="日報を配信済みにマーク",
    dependencies=[Depends(verify_api_key)],
)
async def deliver_reports(
    body: DeliverRequest,
    service: TrackService = Depends(get_track_service),
) -> DeliverReportsResult:
    """PENDING 状態の日報を配信済みにする。実際の Slack 送信は Bot 層が担当。"""
    try:
        return await service.deliver_reports(
            project_id=body.project_id,
            report_date=body.report_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/submit-responses",
    response_model=SubmitResponsesResult,
    summary="日報回答を送信",
    dependencies=[Depends(verify_api_key)],
)
async def submit_responses(
    body: SubmitResponsesRequest,
    service: TrackService = Depends(get_track_service),
) -> SubmitResponsesResult:
    """メンバーの日報回答を受け取り DailyReport に保存する。"""
    inputs = [
        ResponseInput(question_id=r.question_id, response_text=r.response_text)
        for r in body.responses
    ]
    try:
        return await service.submit_responses(
            report_id=body.report_id,
            responses=inputs,
            finalize=body.finalize,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/analyze",
    response_model=AnalyzeResult,
    summary="日報を AI 解析",
    dependencies=[Depends(verify_api_key)],
)
async def analyze_responses(
    body: AnalyzeRequest,
    service: TrackService = Depends(get_track_service),
) -> AnalyzeResult:
    """AI が日報回答を解析してサマリとブロッカーを抽出する。"""
    try:
        return await service.analyze_responses(report_id=body.report_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

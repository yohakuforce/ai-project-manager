"""FastAPI ルーター — Pipeline（リーダー確認ゲート / 手動再実行）エンドポイント。

リーダーは PENDING ゲートを一覧し、解決（proceed / skip）することで後続処理を発火する。
スタンドアップ・総括・final_analysis の手動再実行も提供する
（スケジューラ再起動で未解決ゲートが失われた場合の復旧・運用用）。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.deps import (
    get_gate_service,
    get_standup_service,
    get_status_service,
    get_wrap_up_service,
)
from src.api.middleware import verify_api_key
from src.application.gate.service import GateService, GateView, ResolveResult
from src.application.standup.service import StandupService
from src.application.status.service import ProjectStatusService
from src.application.wrapup.service import WrapUpService
from src.domain.gate.aggregate import GateDecision

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class ResolveGateRequest(BaseModel):
    decision: str = Field(..., description="リーダーの判断: 'proceed' | 'skip'")
    resolved_by: str = Field(..., description="解決したリーダーのユーザー ID")


class ManualRunRequest(BaseModel):
    project_id: str = Field(..., description="AI-PM 内部のプロジェクト UUID")
    target_date: date | None = Field(default=None, description="基準日（省略時は今日）")


@router.get(
    "/{project_id}/gates",
    response_model=list[GateView],
    summary="PENDING のリーダー確認ゲートを一覧",
    dependencies=[Depends(verify_api_key)],
)
async def list_pending_gates(
    project_id: str,
    service: GateService = Depends(get_gate_service),
) -> list[GateView]:
    return await service.list_pending(project_id)


@router.post(
    "/{project_id}/gates/{gate_id}/resolve",
    response_model=ResolveResult,
    summary="ゲートを解決（proceed で後続処理を発火）",
    dependencies=[Depends(verify_api_key)],
)
async def resolve_gate(
    project_id: str,
    gate_id: str,
    body: ResolveGateRequest,
    service: GateService = Depends(get_gate_service),
) -> ResolveResult:
    try:
        decision = GateDecision(body.decision)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="decision は 'proceed' または 'skip' を指定してください。",
        )
    try:
        return await service.resolve(gate_id, decision=decision, resolved_by=body.resolved_by)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/standup",
    summary="スタンドアップを手動実行",
    dependencies=[Depends(verify_api_key)],
)
async def run_standup(
    body: ManualRunRequest,
    service: StandupService = Depends(get_standup_service),
):
    try:
        return await service.run(body.project_id, body.target_date)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/wrap-up",
    summary="当日総括を手動実行（提出状況で分岐）",
    dependencies=[Depends(verify_api_key)],
)
async def run_wrap_up(
    body: ManualRunRequest,
    service: WrapUpService = Depends(get_wrap_up_service),
):
    try:
        return await service.run(body.project_id, body.target_date)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/final-analysis",
    summary="全体ステータス分析＋未割当 DRAFT アサインを手動実行",
    dependencies=[Depends(verify_api_key)],
)
async def run_final_analysis(
    body: ManualRunRequest,
    service: ProjectStatusService = Depends(get_status_service),
):
    try:
        return await service.run_final_analysis(body.project_id, body.target_date)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

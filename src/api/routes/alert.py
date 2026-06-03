"""FastAPI ルーター — Alert エンドポイント。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from src.api.deps import get_alert_service
from src.api.middleware import verify_api_key
from src.application.alert.service import AcknowledgeResult, AlertService, ScanResult

router = APIRouter(prefix="/alerts", tags=["alerts"])


class ScanRequest(BaseModel):
    project_id: str
    scan_date: date | None = Field(default=None, description="スキャン基準日（省略時は今日）")


class AcknowledgeRequest(BaseModel):
    alert_id: str
    acknowledged_by: str = Field(..., description="確認したユーザー ID")


class ResolveRequest(BaseModel):
    alert_id: str


@router.post(
    "/scan",
    response_model=ScanResult,
    summary="プロジェクトをスキャンしてアラートを検出",
    dependencies=[Depends(verify_api_key)],
)
async def scan_project(
    body: ScanRequest,
    service: AlertService = Depends(get_alert_service),
) -> ScanResult:
    """
    タスク遅延・メンバー過負荷・日報未回答をスキャンしてアラートを生成する。
    通常はスケジューラが 30 分毎に自動呼び出しする。
    """
    try:
        return await service.scan_project(
            project_id=body.project_id,
            scan_date=body.scan_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/acknowledge",
    response_model=AcknowledgeResult,
    summary="アラートを確認済みにする",
    dependencies=[Depends(verify_api_key)],
)
async def acknowledge_alert(
    body: AcknowledgeRequest,
    service: AlertService = Depends(get_alert_service),
) -> AcknowledgeResult:
    try:
        return await service.acknowledge_alert(
            alert_id=body.alert_id,
            acknowledged_by=body.acknowledged_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/resolve",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,  # 204 はボディ無し（fastapi 0.115 の厳格チェック対策）
    summary="アラートを解決済みにする",
    dependencies=[Depends(verify_api_key)],
)
async def resolve_alert(
    body: ResolveRequest,
    service: AlertService = Depends(get_alert_service),
) -> None:
    try:
        await service.resolve_alert(alert_id=body.alert_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

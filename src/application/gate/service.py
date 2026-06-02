"""
GateService — リーダー確認ゲートの起票・一覧・解決を司る Application Service。

責務:
  - ゲートの起票（open_*）: WrapUpService 等から呼ばれ、PENDING ゲートを保存する。
  - PENDING ゲートの一覧取得（list_pending）: API / GUI でリーダーに提示する。
  - ゲートの解決（resolve）: リーダーの判断を記録し、判断に応じて後続処理を発火する。
      * WRAP_UP_DECISION + PROCEED → on_wrap_up_proceed（総括生成＋次ゲート起票）
      * WRAP_UP_DECISION + SKIP    → 何もしない（その日の総括は行わない）
      * TASK_STATE_CURRENT + PROCEED → on_task_state_confirmed（final_analysis 発火）
      * TASK_STATE_CURRENT + SKIP   → 何もしない

後続処理は循環依存を避けるため、async コールバック（project_id, gate_date を受け取る）
として注入する。配線層（deps）が実サービスを束ねたコールバックを渡す。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from src.domain.audit.aggregate import AuditAction, AuditLog
from src.domain.audit.repository import AuditLogRepository
from src.domain.gate.aggregate import (
    GateDecision,
    GateType,
    LeaderGate,
    LeaderGateId,
)
from src.domain.gate.repository import LeaderGateRepository

logger = logging.getLogger(__name__)

# 解決後の後続処理コールバック。(project_id, gate_date) を受け取る async 関数。
# 戻り値は問わない（WrapUpResult / FinalAnalysisResult 等を返す実サービスを束ねられるよう Any）。
GateContinuation = Callable[[str, date], Awaitable[Any]]


@dataclass(frozen=True)
class GateView:
    """API / GUI 提示用のゲート表示 DTO。"""

    gate_id: str
    project_id: str
    gate_type: str
    gate_date: str
    status: str
    context: dict


@dataclass(frozen=True)
class ResolveResult:
    """resolve の戻り値。"""

    gate_id: str
    gate_type: str
    decision: str
    continuation_ran: bool


class GateService:
    def __init__(
        self,
        gate_repository: LeaderGateRepository,
        *,
        on_wrap_up_proceed: GateContinuation | None = None,
        on_task_state_confirmed: GateContinuation | None = None,
        audit_repository: AuditLogRepository | None = None,
    ) -> None:
        self._gate_repo = gate_repository
        self._on_wrap_up_proceed = on_wrap_up_proceed
        self._on_task_state_confirmed = on_task_state_confirmed
        self._audit_repo = audit_repository

    def register_continuations(
        self,
        *,
        on_wrap_up_proceed: GateContinuation | None = None,
        on_task_state_confirmed: GateContinuation | None = None,
    ) -> None:
        """構築後に後続ハンドラを登録する（WrapUpService 等との循環依存回避用）。"""
        if on_wrap_up_proceed is not None:
            self._on_wrap_up_proceed = on_wrap_up_proceed
        if on_task_state_confirmed is not None:
            self._on_task_state_confirmed = on_task_state_confirmed

    async def open_gate(
        self,
        *,
        project_id: str,
        gate_type: GateType,
        gate_date: date,
        context: dict | None = None,
    ) -> LeaderGate:
        """PENDING ゲートを起票して保存する。"""
        gate = LeaderGate.create(
            project_id=project_id,
            gate_type=gate_type,
            gate_date=gate_date,
            context=context,
        )
        await self._gate_repo.save(gate)
        await self._record_audit(
            action=AuditAction.GATE_OPENED,
            actor="system",
            project_id=project_id,
            data_ref=str(gate.gate_id),
        )
        logger.info(
            "リーダー確認ゲート起票: gate_id=%s type=%s project=%s",
            gate.gate_id,
            gate_type.value,
            project_id,
        )
        return gate

    async def list_pending(self, project_id: str) -> list[GateView]:
        """プロジェクトの PENDING ゲートを一覧する。"""
        gates = await self._gate_repo.find_pending_by_project(project_id)
        return [self._to_view(g) for g in gates]

    async def resolve(
        self,
        gate_id: str,
        *,
        decision: GateDecision,
        resolved_by: str,
    ) -> ResolveResult:
        """リーダーがゲートを解決し、判断に応じて後続処理を発火する。"""
        import uuid

        gate = await self._gate_repo.find_by_id(LeaderGateId(value=uuid.UUID(gate_id)))
        if gate is None:
            raise ValueError(f"ゲートが見つかりません: {gate_id}")

        gate.resolve(decision=decision, resolved_by=resolved_by)
        await self._gate_repo.save(gate)
        await self._record_audit(
            action=AuditAction.GATE_RESOLVED,
            actor=resolved_by,
            project_id=gate.project_id,
            data_ref=str(gate.gate_id),
        )

        continuation_ran = False
        if decision == GateDecision.PROCEED:
            continuation_ran = await self._dispatch_continuation(gate)

        return ResolveResult(
            gate_id=gate_id,
            gate_type=gate.gate_type.value,
            decision=decision.value,
            continuation_ran=continuation_ran,
        )

    async def _dispatch_continuation(self, gate: LeaderGate) -> bool:
        """PROCEED 解決時の後続処理を発火する。失敗しても解決自体は確定済み。"""
        handler: GateContinuation | None = None
        if gate.gate_type == GateType.WRAP_UP_DECISION:
            handler = self._on_wrap_up_proceed
        elif gate.gate_type == GateType.TASK_STATE_CURRENT:
            handler = self._on_task_state_confirmed

        if handler is None:
            logger.warning(
                "ゲート解決の後続ハンドラが未設定です: type=%s gate_id=%s",
                gate.gate_type.value,
                gate.gate_id,
            )
            return False

        try:
            await handler(gate.project_id, gate.gate_date)
            return True
        except Exception as exc:  # 後続失敗で解決を巻き戻さない（記録のみ）
            logger.error(
                "ゲート後続処理に失敗しました: type=%s gate_id=%s error=%s",
                gate.gate_type.value,
                gate.gate_id,
                exc,
            )
            return False

    @staticmethod
    def _to_view(gate: LeaderGate) -> GateView:
        return GateView(
            gate_id=str(gate.gate_id),
            project_id=gate.project_id,
            gate_type=gate.gate_type.value,
            gate_date=gate.gate_date.isoformat(),
            status=gate.status.value,
            context=gate.context,
        )

    async def _record_audit(
        self,
        *,
        action: AuditAction,
        actor: str,
        project_id: str | None,
        data_ref: str | None,
    ) -> None:
        if self._audit_repo is None:
            return
        try:
            await self._audit_repo.append(
                AuditLog.create(
                    actor=actor,
                    action=action,
                    project_id=project_id,
                    data_ref=data_ref,
                )
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("監査ログ記録に失敗しました: action=%s error=%s", action, exc)

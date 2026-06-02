"""LeaderGate 集約ルート。

リーダーの人間判断を待つ「ゲート」を表す。日次パイプラインのうち、AI が
自動で先に進めず人間（リーダー）の確認・判断を必要とするステップで起票され、
リーダーが解決（resolve）すると後続処理が発火する。

ゲートの種類:
  - WRAP_UP_DECISION  : 17:30 時点で日報未提出が残るとき、「総括を進めてよいか」を問う判断ゲート
  - TASK_STATE_CURRENT: 総括後、「タスク状態は最新か」を問う確認ゲート。確認されると
                        final_analysis（全体ステータスレポート＋未割当 DRAFT アサイン）が発火する

永続化に関する注意:
  MVP ではインメモリ実装のみ。プロセス再起動で未解決ゲートは失われる。
  その場合リーダーは手動再実行 API（standup / wrap-up / final-analysis）で復旧できる。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum


@dataclass(frozen=True)
class LeaderGateId:
    value: uuid.UUID

    @classmethod
    def generate(cls) -> LeaderGateId:
        return cls(value=uuid.uuid4())

    def __str__(self) -> str:
        return str(self.value)


class GateType(str, Enum):
    """ゲートの種類。"""

    WRAP_UP_DECISION = "wrap_up_decision"  # 未提出ありでも総括するか（リーダー判断）
    TASK_STATE_CURRENT = "task_state_current"  # タスク状態は最新か（リーダー確認）


class GateStatus(str, Enum):
    PENDING = "pending"  # リーダーの判断待ち
    RESOLVED = "resolved"  # リーダーが解決済み


class GateDecision(str, Enum):
    """リーダーの判断結果。"""

    PROCEED = "proceed"  # 進める（総括する / 確認した）
    SKIP = "skip"  # 進めない（総括しない）


class GateError(Exception):
    """ゲート操作の不正（解決済みの再解決など）。"""


@dataclass
class LeaderGate:
    """リーダー確認ゲートの集約ルート。

    不変条件:
      - PENDING のときのみ resolve できる。RESOLVED への再解決は GateError。
      - resolve すると status=RESOLVED、decision / resolved_by / resolved_at が確定する。
    """

    gate_id: LeaderGateId
    project_id: str
    gate_type: GateType
    gate_date: date
    status: GateStatus = GateStatus.PENDING
    # 起票時の補助情報（未提出者名・注目タスク等）。表示・監査用。
    context: dict = field(default_factory=dict)
    decision: GateDecision | None = None
    resolved_by: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        gate_type: GateType,
        gate_date: date,
        context: dict | None = None,
    ) -> LeaderGate:
        return cls(
            gate_id=LeaderGateId.generate(),
            project_id=project_id,
            gate_type=gate_type,
            gate_date=gate_date,
            context=context or {},
        )

    @property
    def is_pending(self) -> bool:
        return self.status == GateStatus.PENDING

    def resolve(self, *, decision: GateDecision, resolved_by: str) -> None:
        """リーダーがゲートを解決する。PENDING のときのみ可能。"""
        if self.status != GateStatus.PENDING:
            raise GateError(
                f"既に解決済みのゲートは再解決できません: gate_id={self.gate_id} status={self.status}"
            )
        self.status = GateStatus.RESOLVED
        self.decision = decision
        self.resolved_by = resolved_by
        self.resolved_at = datetime.now(UTC)

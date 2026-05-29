"""監査ログを記録する LLMAdapter デコレータ。

任意の LLMAdapter をラップし、generate() 呼び出しごとに
AuditAction.LLM_CALL を AuditLogRepository に追記する。

設計方針:
  - LLMAdapter プロトコルに対する委譲（Decorator パターン）。
  - 入力プロンプトの生データは保存せず SHA-256 ハッシュのみ記録する
    （security-governance-v1.md §6-1 「入力データはハッシュで匿名化」）。
  - 監査記録の失敗は LLM 呼び出し結果には影響しない（warning log のみ）。
  - 推定コストは設定可能な単価テーブル（USD / 1k tokens）から算出。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from src.domain.audit.aggregate import AuditAction, AuditLog, TokenUsage
from src.domain.audit.repository import AuditLogRepository
from src.infrastructure.llm.adapter import LLMAdapter, LLMResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CostRate:
    """1k tokens あたりの推定 USD 単価。"""

    prompt_per_1k: float
    completion_per_1k: float

    def estimate(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens / 1000.0) * self.prompt_per_1k + (
            completion_tokens / 1000.0
        ) * self.completion_per_1k


# モデル別の参考単価（2026-05 時点の Anthropic 公開価格）。
# claude-code / ollama 等のサブスク経由はゼロ単価として扱う（コストは別途月額）。
_DEFAULT_COST_RATES: dict[str, CostRate] = {
    "claude-opus-4-7": CostRate(prompt_per_1k=0.015, completion_per_1k=0.075),
    "claude-sonnet-4-6": CostRate(prompt_per_1k=0.003, completion_per_1k=0.015),
    "claude-haiku-4-5": CostRate(prompt_per_1k=0.0008, completion_per_1k=0.004),
}
_ZERO_RATE = CostRate(prompt_per_1k=0.0, completion_per_1k=0.0)


def _hash_prompt(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rate = _DEFAULT_COST_RATES.get(model.lower(), _ZERO_RATE)
    return rate.estimate(prompt_tokens, completion_tokens)


class AuditingLLMAdapter:
    """LLMAdapter ラッパー: generate() ごとに LLM_CALL 監査ログを追記する。

    Notes:
        Protocol を implement するため、属性は public な ``model_name`` プロパティで委譲する。
    """

    def __init__(
        self,
        inner: LLMAdapter,
        audit_repository: AuditLogRepository,
        *,
        actor: str = "ai-agent",
        project_id_resolver=None,
    ) -> None:
        self._inner = inner
        self._audit_repo = audit_repository
        self._actor = actor
        # project_id_resolver は呼び出し時の project 文脈を渡したい場合に使う想定。
        # MVP では None（=project_id を残さない LLM_CALL ログ）でよい。
        self._project_id_resolver = project_id_resolver

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> LLMResponse:
        response = await self._inner.generate(
            prompt, max_tokens=max_tokens, temperature=temperature
        )
        await self._record_llm_call(prompt, response)
        return response

    async def _record_llm_call(self, prompt: str, response: LLMResponse) -> None:
        try:
            project_id = (
                self._project_id_resolver() if self._project_id_resolver is not None else None
            )
            await self._audit_repo.append(
                AuditLog.create(
                    actor=self._actor,
                    action=AuditAction.LLM_CALL,
                    project_id=project_id,
                    llm_model=response.model,
                    token_usage=TokenUsage(
                        prompt_tokens=response.prompt_tokens,
                        completion_tokens=response.completion_tokens,
                        estimated_cost_usd=_estimate_cost(
                            response.model,
                            response.prompt_tokens,
                            response.completion_tokens,
                        ),
                    ),
                    input_hash=_hash_prompt(prompt),
                )
            )
        except Exception as exc:  # pragma: no cover - 想定外の I/O 失敗
            logger.warning("LLM_CALL 監査ログの記録に失敗しました: %s", exc)

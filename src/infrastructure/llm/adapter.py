"""
LLM アダプタ層。
security-governance-v1.md Section 0 B. AI エージェントモデル抽象化 に準拠。
環境変数 LLM_PROVIDER で Claude / Antigravity / Codex を切り替える。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@runtime_checkable
class LLMAdapter(Protocol):
    """LLM プロバイダ抽象化インターフェース。"""

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> LLMResponse: ...

    @property
    def model_name(self) -> str: ...

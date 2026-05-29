"""
Anthropic Claude API アダプタ。

DEPRECATED (2026-05-15):
  課金 API（Anthropic API 従量課金）を使用するため、本番では使用しない。
  LLM_PROVIDER=claude-code を使用すること（ClaudeCodeAdapter）。
  テスト・緊急時のフォールバック用途のみ残す。
"""

from __future__ import annotations

import warnings

import anthropic

from .adapter import LLMAdapter, LLMResponse


class ClaudeAdapter:
    """Anthropic Claude API を呼び出す LLM アダプタ実装。"""

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022") -> None:
        warnings.warn(
            "ClaudeAdapter は課金 API を使用するため非推奨です。"
            "LLM_PROVIDER=claude-code に切り替えて ClaudeCodeAdapter を使用してください。",
            DeprecationWarning,
            stacklevel=2,
        )
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> LLMResponse:
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        content_text = message.content[0].text if message.content else ""
        return LLMResponse(
            content=content_text,
            model=self._model,
            prompt_tokens=message.usage.input_tokens,
            completion_tokens=message.usage.output_tokens,
        )


# isinstance チェックで LLMAdapter プロトコルを満たすことを静的に確認
_: LLMAdapter = ClaudeAdapter.__new__(ClaudeAdapter)  # type: ignore[assignment]

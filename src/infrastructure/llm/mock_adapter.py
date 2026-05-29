"""
LLM モックアダプタ。
テスト時・Context-Hub 本体なし環境で使用する。
"""

from __future__ import annotations

from .adapter import LLMResponse


class MockLLMAdapter:
    """
    テスト / ローカル開発用のモック LLM アダプタ。
    固定レスポンスを返す。テストで monkey-patch して応答をカスタマイズできる。
    """

    def __init__(self, fixed_response: str = "Mock LLM response") -> None:
        self._fixed_response = fixed_response

    @property
    def model_name(self) -> str:
        return "mock-llm"

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> LLMResponse:
        return LLMResponse(
            content=self._fixed_response,
            model="mock-llm",
            prompt_tokens=len(prompt.split()),
            completion_tokens=len(self._fixed_response.split()),
        )

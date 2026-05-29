"""
LLM アダプタのユニットテスト。
MockLLMAdapter の動作と LLMAdapter プロトコル準拠を検証する。
"""

from __future__ import annotations

import pytest

from src.infrastructure.llm import LLMAdapter, LLMResponse, MockLLMAdapter


@pytest.mark.asyncio
class TestMockLLMAdapter:
    async def test_generate_returns_llm_response(self) -> None:
        adapter = MockLLMAdapter(fixed_response="テスト応答")
        result = await adapter.generate("テストプロンプト")
        assert isinstance(result, LLMResponse)
        assert result.content == "テスト応答"

    async def test_generate_returns_mock_model_name(self) -> None:
        adapter = MockLLMAdapter()
        result = await adapter.generate("prompt")
        assert result.model == "mock-llm"

    async def test_model_name_property(self) -> None:
        adapter = MockLLMAdapter()
        assert adapter.model_name == "mock-llm"

    async def test_total_tokens_is_sum(self) -> None:
        adapter = MockLLMAdapter(fixed_response="one two three")
        result = await adapter.generate("a b c d e")
        assert result.total_tokens == result.prompt_tokens + result.completion_tokens

    def test_mock_adapter_satisfies_llm_adapter_protocol(self) -> None:
        adapter = MockLLMAdapter()
        assert isinstance(adapter, LLMAdapter)

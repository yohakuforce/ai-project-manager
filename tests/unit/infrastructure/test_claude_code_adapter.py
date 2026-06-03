"""
ClaudeCodeAdapter のユニットテスト。
subprocess 呼び出しをモックして動作を検証する。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.infrastructure.llm.adapter import LLMAdapter, LLMResponse
from src.infrastructure.llm.claude_code_adapter import ClaudeCodeAdapter


class TestClaudeCodeAdapterInit:
    def test_raises_if_cli_not_found_on_path(self) -> None:
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="Claude Code CLI"):
                ClaudeCodeAdapter()

    def test_accepts_explicit_cli_path(self) -> None:
        adapter = ClaudeCodeAdapter(cli_path="/usr/local/bin/claude")
        assert adapter._cli_path == "/usr/local/bin/claude"

    def test_model_name(self) -> None:
        adapter = ClaudeCodeAdapter(cli_path="/usr/local/bin/claude")
        assert adapter.model_name == "claude-code-cli"

    def test_satisfies_llm_adapter_protocol(self) -> None:
        adapter = ClaudeCodeAdapter(cli_path="/usr/local/bin/claude")
        assert isinstance(adapter, LLMAdapter)


class TestClaudeCodeAdapterGenerate:
    def _make_adapter(self) -> ClaudeCodeAdapter:
        return ClaudeCodeAdapter(cli_path="/usr/local/bin/claude", timeout_seconds=30)

    async def _mock_subprocess(self, stdout: bytes, returncode: int = 0) -> tuple:
        """subprocess の mock を返す。"""
        proc = AsyncMock()
        proc.returncode = returncode
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        return proc

    @pytest.mark.asyncio
    async def test_returns_llm_response_with_json_output(self) -> None:
        adapter = self._make_adapter()
        json_output = json.dumps({"type": "result", "result": "タスク抽出完了"}).encode()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = await self._mock_subprocess(json_output)
            result = await adapter.generate("テストプロンプト")

        assert isinstance(result, LLMResponse)
        assert result.content == "タスク抽出完了"
        assert result.model == "claude-code-cli"

    @pytest.mark.asyncio
    async def test_returns_raw_output_when_json_parse_fails(self) -> None:
        adapter = self._make_adapter()
        raw_output = b"plain text response"

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = await self._mock_subprocess(raw_output)
            result = await adapter.generate("prompt")

        assert result.content == "plain text response"

    @pytest.mark.asyncio
    async def test_raises_on_nonzero_exit_code(self) -> None:
        adapter = self._make_adapter()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.returncode = 1
            proc.communicate = AsyncMock(return_value=(b"", b"error message"))
            mock_exec.return_value = proc

            with pytest.raises(RuntimeError, match="エラーを返しました"):
                await adapter.generate("prompt")

    @pytest.mark.asyncio
    async def test_raises_on_timeout(self) -> None:
        adapter = self._make_adapter()

        async def slow_communicate():
            await asyncio.sleep(100)
            return b"", b""

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.communicate = slow_communicate
            mock_exec.return_value = proc

            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                with pytest.raises(RuntimeError, match="タイムアウト"):
                    await adapter.generate("prompt")

    @pytest.mark.asyncio
    async def test_raises_when_cli_not_found_at_runtime(self) -> None:
        adapter = self._make_adapter()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="Claude Code CLI"):
                await adapter.generate("prompt")

    @pytest.mark.asyncio
    async def test_token_count_estimated_from_text_length(self) -> None:
        adapter = self._make_adapter()
        prompt = "a" * 400  # 400文字 → 100トークン
        response_text = "b" * 200  # 200文字 → 50トークン

        json_output = json.dumps({"result": response_text}).encode()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = await self._mock_subprocess(json_output)
            result = await adapter.generate(prompt)

        assert result.prompt_tokens == 100
        assert result.completion_tokens == 50

    def test_parse_content_empty_string(self) -> None:
        assert ClaudeCodeAdapter._parse_content("") == ""

    def test_parse_content_json_with_result_key(self) -> None:
        raw = json.dumps({"type": "result", "result": "answer"})
        assert ClaudeCodeAdapter._parse_content(raw) == "answer"

    def test_parse_content_json_with_content_key(self) -> None:
        raw = json.dumps({"content": "text content"})
        assert ClaudeCodeAdapter._parse_content(raw) == "text content"

    def test_parse_content_plain_text(self) -> None:
        assert ClaudeCodeAdapter._parse_content("plain text") == "plain text"


class TestLLMFactory:
    def test_creates_claude_code_adapter_for_claude_code_provider(self) -> None:
        from src.config import get_settings
        from src.infrastructure.llm.factory import create_llm_adapter

        get_settings.cache_clear()
        with patch.dict("os.environ", {"LLM_PROVIDER": "claude-code"}):
            with patch("shutil.which", return_value="/usr/local/bin/claude"):
                get_settings.cache_clear()
                adapter = create_llm_adapter()
                assert isinstance(adapter, ClaudeCodeAdapter)
        get_settings.cache_clear()

    def test_creates_mock_adapter_for_mock_provider(self) -> None:
        from src.config import get_settings
        from src.infrastructure.llm.factory import create_llm_adapter
        from src.infrastructure.llm.mock_adapter import MockLLMAdapter

        get_settings.cache_clear()
        with patch.dict("os.environ", {"LLM_PROVIDER": "mock"}):
            get_settings.cache_clear()
            adapter = create_llm_adapter()
            assert isinstance(adapter, MockLLMAdapter)
        get_settings.cache_clear()

    def test_raises_for_unknown_provider(self) -> None:
        from src.config import get_settings
        from src.infrastructure.llm.factory import create_llm_adapter

        get_settings.cache_clear()
        with patch.dict("os.environ", {"LLM_PROVIDER": "unknown_provider"}):
            get_settings.cache_clear()
            with pytest.raises(ValueError, match="未知の LLM_PROVIDER"):
                create_llm_adapter()
        get_settings.cache_clear()

    def test_creates_codex_adapter_for_codex_provider(self) -> None:
        from src.config import get_settings
        from src.infrastructure.llm.codex_adapter import CodexAdapter
        from src.infrastructure.llm.factory import create_llm_adapter

        get_settings.cache_clear()
        with patch.dict("os.environ", {"LLM_PROVIDER": "codex"}):
            with patch("shutil.which", return_value="/usr/local/bin/codex"):
                get_settings.cache_clear()
                adapter = create_llm_adapter()
                assert isinstance(adapter, CodexAdapter)
        get_settings.cache_clear()

    def test_creates_antigravity_adapter_for_antigravity_provider(self) -> None:
        from src.config import get_settings
        from src.infrastructure.llm.antigravity_adapter import AntigravityAdapter
        from src.infrastructure.llm.factory import create_llm_adapter

        get_settings.cache_clear()
        with patch.dict("os.environ", {"LLM_PROVIDER": "antigravity"}):
            with patch("shutil.which", return_value="/usr/local/bin/antigravity"):
                get_settings.cache_clear()
                adapter = create_llm_adapter()
                assert isinstance(adapter, AntigravityAdapter)
        get_settings.cache_clear()

    def test_creates_local_adapter_for_local_provider(self) -> None:
        from src.config import get_settings
        from src.infrastructure.llm.factory import create_llm_adapter
        from src.infrastructure.llm.local_adapter import LocalLLMAdapter

        get_settings.cache_clear()
        with patch.dict("os.environ", {"LLM_PROVIDER": "local"}):
            get_settings.cache_clear()
            adapter = create_llm_adapter()
            assert isinstance(adapter, LocalLLMAdapter)
        get_settings.cache_clear()

    def test_creates_ollama_adapter_for_ollama_provider(self) -> None:
        from src.config import get_settings
        from src.infrastructure.llm.factory import create_llm_adapter
        from src.infrastructure.llm.ollama_adapter import OllamaAdapter

        get_settings.cache_clear()
        with patch.dict("os.environ", {"LLM_PROVIDER": "ollama"}):
            get_settings.cache_clear()
            adapter = create_llm_adapter()
            assert isinstance(adapter, OllamaAdapter)
        get_settings.cache_clear()

"""codex / antigravity / local / ollama LLM アダプタのユニットテスト。

subprocess（CLI 系）と httpx（HTTP 系）をモックして動作を検証する。
実 CLI / 実サーバには接続しない。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.infrastructure.llm._cli_runner import (
    estimate_tokens,
    parse_cli_content,
    resolve_cli,
)
from src.infrastructure.llm.adapter import LLMAdapter, LLMResponse
from src.infrastructure.llm.antigravity_adapter import AntigravityAdapter
from src.infrastructure.llm.codex_adapter import CodexAdapter
from src.infrastructure.llm.local_adapter import LocalLLMAdapter
from src.infrastructure.llm.ollama_adapter import OllamaAdapter

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _mock_subprocess(stdout: bytes, returncode: int = 0) -> AsyncMock:
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


def _mock_httpx_client(response: MagicMock | None, *, post_side_effect: object = None) -> MagicMock:
    """httpx.AsyncClient(...) as client の async context manager モックを返す。"""
    client = AsyncMock()
    if post_side_effect is not None:
        client.post = AsyncMock(side_effect=post_side_effect)
    else:
        client.post = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    # client を後から参照できるよう保持
    ctx._client = client
    return ctx


def _http_response(status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# _cli_runner
# ---------------------------------------------------------------------------


class TestCliRunner:
    def test_resolve_cli_prefers_explicit_path(self) -> None:
        assert resolve_cli("/bin/foo", "foo", "hint") == "/bin/foo"

    def test_resolve_cli_falls_back_to_which(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/foo"):
            assert resolve_cli("", "foo", "hint") == "/usr/bin/foo"

    def test_resolve_cli_raises_when_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="hint"):
                resolve_cli("", "foo", "hint")

    def test_parse_cli_content_json_result(self) -> None:
        assert parse_cli_content(json.dumps({"result": "ok"})) == "ok"

    def test_parse_cli_content_json_content(self) -> None:
        assert parse_cli_content(json.dumps({"content": "body"})) == "body"

    def test_parse_cli_content_plain(self) -> None:
        assert parse_cli_content("plain") == "plain"

    def test_parse_cli_content_empty(self) -> None:
        assert parse_cli_content("") == ""

    def test_estimate_tokens_minimum_one(self) -> None:
        assert estimate_tokens("") == 1
        assert estimate_tokens("a" * 400) == 100


# ---------------------------------------------------------------------------
# CodexAdapter
# ---------------------------------------------------------------------------


class TestCodexAdapter:
    def test_init_raises_when_cli_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="Codex CLI"):
                CodexAdapter()

    def test_model_name(self) -> None:
        adapter = CodexAdapter(cli_path="/usr/local/bin/codex")
        assert adapter.model_name == "codex-cli"

    def test_satisfies_protocol(self) -> None:
        adapter = CodexAdapter(cli_path="/usr/local/bin/codex")
        assert isinstance(adapter, LLMAdapter)

    @pytest.mark.asyncio
    async def test_generate_returns_response(self) -> None:
        adapter = CodexAdapter(cli_path="/usr/local/bin/codex", timeout_seconds=30)
        out = json.dumps({"result": "コーデックス応答"}).encode()
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = await _mock_subprocess(out)
            result = await adapter.generate("prompt")
        assert isinstance(result, LLMResponse)
        assert result.content == "コーデックス応答"
        assert result.model == "codex-cli"

    @pytest.mark.asyncio
    async def test_generate_raises_on_nonzero(self) -> None:
        adapter = CodexAdapter(cli_path="/usr/local/bin/codex")
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.returncode = 2
            proc.communicate = AsyncMock(return_value=(b"", b"boom"))
            mock_exec.return_value = proc
            with pytest.raises(RuntimeError, match="エラーを返しました"):
                await adapter.generate("prompt")


# ---------------------------------------------------------------------------
# AntigravityAdapter
# ---------------------------------------------------------------------------


class TestAntigravityAdapter:
    def test_init_raises_when_cli_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="Antigravity CLI"):
                AntigravityAdapter()

    def test_model_name(self) -> None:
        adapter = AntigravityAdapter(cli_path="/usr/local/bin/antigravity")
        assert adapter.model_name == "antigravity-cli"

    def test_satisfies_protocol(self) -> None:
        adapter = AntigravityAdapter(cli_path="/usr/local/bin/antigravity")
        assert isinstance(adapter, LLMAdapter)

    @pytest.mark.asyncio
    async def test_generate_uses_custom_prompt_flag(self) -> None:
        adapter = AntigravityAdapter(
            cli_path="/opt/antigravity", prompt_flag="--prompt", timeout_seconds=30
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = await _mock_subprocess(b"plain answer")
            result = await adapter.generate("hi")
        assert result.content == "plain answer"
        # コマンドに custom flag が使われていること
        called_args = mock_exec.call_args[0]
        assert called_args[0] == "/opt/antigravity"
        assert called_args[1] == "--prompt"
        assert called_args[2] == "hi"


# ---------------------------------------------------------------------------
# LocalLLMAdapter (OpenAI-compatible)
# ---------------------------------------------------------------------------


class TestLocalLLMAdapter:
    def test_model_name(self) -> None:
        adapter = LocalLLMAdapter(model="qwen2")
        assert adapter.model_name == "local:qwen2"

    def test_satisfies_protocol(self) -> None:
        assert isinstance(LocalLLMAdapter(), LLMAdapter)

    @pytest.mark.asyncio
    async def test_generate_parses_openai_response(self) -> None:
        adapter = LocalLLMAdapter(model="llama3")
        body = {
            "choices": [{"message": {"content": "ローカル応答"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }
        ctx = _mock_httpx_client(_http_response(200, body))
        with patch("httpx.AsyncClient", return_value=ctx):
            result = await adapter.generate("prompt")
        assert result.content == "ローカル応答"
        assert result.model == "llama3"
        assert result.prompt_tokens == 11
        assert result.completion_tokens == 7

    @pytest.mark.asyncio
    async def test_generate_sets_auth_header_when_key_present(self) -> None:
        adapter = LocalLLMAdapter(api_key="secret-key")
        body = {"choices": [{"message": {"content": "x"}}], "usage": {}}
        ctx = _mock_httpx_client(_http_response(200, body))
        with patch("httpx.AsyncClient", return_value=ctx):
            await adapter.generate("p")
        headers = ctx._client.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer secret-key"

    @pytest.mark.asyncio
    async def test_generate_raises_on_http_error(self) -> None:
        adapter = LocalLLMAdapter()
        ctx = _mock_httpx_client(_http_response(500, text="server error"))
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="HTTP 500"):
                await adapter.generate("p")

    @pytest.mark.asyncio
    async def test_generate_raises_on_connect_error(self) -> None:
        adapter = LocalLLMAdapter()
        ctx = _mock_httpx_client(None, post_side_effect=httpx.ConnectError("refused"))
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="接続できません"):
                await adapter.generate("p")

    @pytest.mark.asyncio
    async def test_generate_raises_on_malformed_body(self) -> None:
        adapter = LocalLLMAdapter()
        ctx = _mock_httpx_client(_http_response(200, {"unexpected": True}))
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="解釈できません"):
                await adapter.generate("p")


# ---------------------------------------------------------------------------
# OllamaAdapter (native)
# ---------------------------------------------------------------------------


class TestOllamaAdapter:
    def test_model_name(self) -> None:
        adapter = OllamaAdapter(model="llama3")
        assert adapter.model_name == "ollama:llama3"

    def test_satisfies_protocol(self) -> None:
        assert isinstance(OllamaAdapter(), LLMAdapter)

    @pytest.mark.asyncio
    async def test_generate_parses_native_response(self) -> None:
        adapter = OllamaAdapter(model="llama3")
        body = {"response": "Ollama 応答", "prompt_eval_count": 5, "eval_count": 9}
        ctx = _mock_httpx_client(_http_response(200, body))
        with patch("httpx.AsyncClient", return_value=ctx):
            result = await adapter.generate("prompt")
        assert result.content == "Ollama 応答"
        assert result.prompt_tokens == 5
        assert result.completion_tokens == 9

    @pytest.mark.asyncio
    async def test_generate_raises_on_empty(self) -> None:
        adapter = OllamaAdapter()
        ctx = _mock_httpx_client(_http_response(200, {"response": ""}))
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="空の応答"):
                await adapter.generate("p")

    @pytest.mark.asyncio
    async def test_generate_raises_on_connect_error(self) -> None:
        adapter = OllamaAdapter()
        ctx = _mock_httpx_client(None, post_side_effect=httpx.ConnectError("refused"))
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="接続できません"):
                await adapter.generate("p")

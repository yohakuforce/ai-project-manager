"""CodexAdapter — Codex CLI を subprocess 経由で呼び出す（LLM_PROVIDER=codex）。

Codex はローカルに認証済みインストールされた CLI 経由で使うサブスク AI。課金 API
は使わない。非対話モードで呼び出す:
    codex -q "<prompt>"

設定: CODEX_CLI_PATH（空なら PATH から自動検出）/ CODEX_TIMEOUT_SECONDS（既定 120）。
"""

from __future__ import annotations

from ._cli_runner import estimate_tokens, parse_cli_content, resolve_cli, run_cli
from .adapter import LLMAdapter, LLMResponse

_CLI_NOT_FOUND_MSG = (
    "Codex CLI が見つかりません。`codex` コマンドがインストール・認証済みで "
    "PATH に存在するか、CODEX_CLI_PATH にフルパスを設定してください。"
)


class CodexAdapter:
    """Codex CLI を subprocess（`codex -q`）で呼び出す LLM アダプタ。

    Note:
        max_tokens / temperature は CLI 側で制御不可のため受け取るが無視する。
    """

    _MODEL_NAME = "codex-cli"

    def __init__(self, cli_path: str | None = None, timeout_seconds: int = 120) -> None:
        self._cli_path = resolve_cli(cli_path or "", "codex", _CLI_NOT_FOUND_MSG)
        self._timeout = timeout_seconds

    @property
    def model_name(self) -> str:
        return self._MODEL_NAME

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> LLMResponse:
        raw = await run_cli(
            [self._cli_path, "-q", prompt],
            self._timeout,
            "Codex CLI",
            _CLI_NOT_FOUND_MSG,
        )
        content = parse_cli_content(raw)
        return LLMResponse(
            content=content,
            model=self._MODEL_NAME,
            prompt_tokens=estimate_tokens(prompt),
            completion_tokens=estimate_tokens(content),
        )


# isinstance チェックで LLMAdapter プロトコルを満たすことを確認
_: LLMAdapter = CodexAdapter.__new__(CodexAdapter)  # type: ignore[assignment]

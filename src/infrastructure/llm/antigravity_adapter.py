"""AntigravityAdapter — Antigravity CLI を subprocess 経由で呼び出す
（LLM_PROVIDER=antigravity）。

Antigravity はローカルに認証済みインストールされた CLI 経由で使うサブスク AI。
課金 API は使わない。CLI の起動方法は環境により異なりうるため、バイナリパス
（ANTIGRAVITY_CLI_PATH）とプロンプト用フラグ（ANTIGRAVITY_PROMPT_FLAG, 既定 -p）を
設定で差し替えられる:
    <antigravity> <flag> "<prompt>"
"""

from __future__ import annotations

from ._cli_runner import estimate_tokens, parse_cli_content, resolve_cli, run_cli
from .adapter import LLMAdapter, LLMResponse

_CLI_NOT_FOUND_MSG = (
    "Antigravity CLI が見つかりません。CLI をインストールして `antigravity` を PATH に "
    "通すか、ANTIGRAVITY_CLI_PATH にフルパスを設定してください。"
)


class AntigravityAdapter:
    """Antigravity CLI を subprocess で呼び出す LLM アダプタ。

    Note:
        max_tokens / temperature は CLI 側で制御不可のため受け取るが無視する。
    """

    _MODEL_NAME = "antigravity-cli"

    def __init__(
        self,
        cli_path: str | None = None,
        prompt_flag: str = "-p",
        timeout_seconds: int = 120,
    ) -> None:
        self._cli_path = resolve_cli(cli_path or "", "antigravity", _CLI_NOT_FOUND_MSG)
        self._prompt_flag = prompt_flag or "-p"
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
            [self._cli_path, self._prompt_flag, prompt],
            self._timeout,
            "Antigravity CLI",
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
_: LLMAdapter = AntigravityAdapter.__new__(AntigravityAdapter)  # type: ignore[assignment]

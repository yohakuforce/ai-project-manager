"""
ClaudeCodeAdapter — subprocess 経由で `claude -p` を呼び出す LLM アダプタ。

方針（2026-05-15 確定）:
- 課金 API（Anthropic API）は使用しない
- Claude Code CLI（koya サブスク契約）を subprocess 経由で呼び出す
- LLM_PROVIDER=claude-code のときに使用する
"""

from __future__ import annotations

import asyncio
import json
import shutil

from .adapter import LLMAdapter, LLMResponse

# claude CLI が見つからない場合のフォールバックメッセージ
_CLI_NOT_FOUND_MSG = (
    "Claude Code CLI が見つかりません。`claude` コマンドがインストールされているか確認してください。"
    "（https://docs.anthropic.com/claude-code）"
)


class ClaudeCodeAdapter:
    """
    Claude Code CLI を subprocess 経由で呼び出す LLM アダプタ。

    Usage:
        adapter = ClaudeCodeAdapter()
        response = await adapter.generate("タスクを抽出してください：...")

    Subprocess コマンド例:
        claude -p "プロンプトテキスト" --output-format json

    注意:
    - `claude` コマンドが PATH に存在することが前提。
    - 実行環境が Windows の場合は WSL2 または Docker 内での実行を想定。
    - max_tokens / temperature は Claude Code CLI では制御不可のため受け取るが無視する。
    """

    _MODEL_NAME = "claude-code-cli"
    # JSON 出力が取れない場合の文字コスト推定係数（トークン ≒ 文字数 / 4）
    _TOKEN_CHARS_RATIO = 4

    def __init__(self, cli_path: str | None = None, timeout_seconds: int = 120) -> None:
        """
        Args:
            cli_path: `claude` コマンドのフルパス。None の場合は PATH から探す。
            timeout_seconds: サブプロセスのタイムアウト秒数。
        """
        self._cli_path = cli_path or self._resolve_cli_path()
        self._timeout = timeout_seconds

    @staticmethod
    def _resolve_cli_path() -> str:
        path = shutil.which("claude")
        if path is None:
            raise RuntimeError(_CLI_NOT_FOUND_MSG)
        return path

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
        """
        Claude Code CLI を呼び出してレスポンスを返す。

        Args:
            prompt: LLM に送るプロンプト文字列。
            max_tokens: 無視（CLI では制御不可）。
            temperature: 無視（CLI では制御不可）。

        Returns:
            LLMResponse（content にテキスト、token_usage は推定値）

        Raises:
            RuntimeError: CLI 実行に失敗した場合。
        """
        cmd = [self._cli_path, "-p", prompt, "--output-format", "json"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            raise RuntimeError(f"Claude Code CLI がタイムアウトしました（{self._timeout}秒）。")
        except FileNotFoundError:
            raise RuntimeError(_CLI_NOT_FOUND_MSG)

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Claude Code CLI がエラーを返しました（終了コード {proc.returncode}）: {err_text}"
            )

        raw_output = stdout.decode("utf-8", errors="replace").strip()
        content = self._parse_content(raw_output)
        prompt_tokens = max(1, len(prompt) // self._TOKEN_CHARS_RATIO)
        completion_tokens = max(1, len(content) // self._TOKEN_CHARS_RATIO)

        return LLMResponse(
            content=content,
            model=self._MODEL_NAME,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    @staticmethod
    def _parse_content(raw_output: str) -> str:
        """
        CLI の JSON 出力からテキスト部分を取り出す。
        JSON パースに失敗した場合は raw_output をそのまま返す。

        Claude Code CLI の --output-format json は以下の形式を返す:
            {"type": "result", "result": "...", ...}
        """
        if not raw_output:
            return ""
        try:
            data = json.loads(raw_output)
            # result キーがあればそちらを使う
            if isinstance(data, dict):
                return str(data.get("result") or data.get("content") or raw_output)
        except (json.JSONDecodeError, ValueError):
            pass
        return raw_output


# isinstance チェックで LLMAdapter プロトコルを満たすことを確認
_: LLMAdapter = ClaudeCodeAdapter.__new__(ClaudeCodeAdapter)  # type: ignore[assignment]

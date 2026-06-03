"""
LLM アダプタのファクトリ。
環境変数 LLM_PROVIDER に基づいてアダプタインスタンスを生成する。

2026-05-15:
  - claude-code (ClaudeCodeAdapter) を追加・デフォルト化
  - ClaudeAdapter（課金 API）は非推奨

2026-06-03:
  - codex / antigravity / ollama / local を実装（スタブから昇格）
  - ローカル LLM は Ollama 限定でなく OpenAI 互換の汎用アダプタ（local）を追加
"""

from __future__ import annotations

from src.config import get_settings

from .adapter import LLMAdapter
from .antigravity_adapter import AntigravityAdapter
from .claude_code_adapter import ClaudeCodeAdapter
from .codex_adapter import CodexAdapter
from .local_adapter import LocalLLMAdapter
from .mock_adapter import MockLLMAdapter
from .ollama_adapter import OllamaAdapter

# 課金 API アダプタは遅延インポートで非推奨警告を出す
# from .claude_adapter import ClaudeAdapter  # DEPRECATED

_SUPPORTED_PROVIDERS = (
    "claude-code",
    "codex",
    "antigravity",
    "local",
    "ollama",
    "mock",
)


def create_llm_adapter() -> LLMAdapter:
    """
    LLM_PROVIDER 環境変数に基づいてアダプタを生成する。

    対応プロバイダ（いずれも課金 API なし）:
      - claude-code  : Claude Code CLI（subprocess）。サブスク AI。デフォルト。
      - codex        : Codex CLI（subprocess）。サブスク AI。
      - antigravity  : Antigravity CLI（subprocess）。サブスク AI。
      - local        : OpenAI 互換ローカル LLM（Ollama/LM Studio/vLLM/llama.cpp 等）。
      - ollama       : ローカル Ollama サーバ（native API）。完全オフライン。
      - mock         : テスト用モック。

    非推奨（削除予定）:
      - claude       : Anthropic API 直接呼び出し（課金 API）。本番使用禁止。
    """
    settings = get_settings()
    provider = settings.llm_provider.lower()

    if provider == "claude-code":
        return ClaudeCodeAdapter(
            cli_path=settings.claude_code_cli_path or None,
            timeout_seconds=settings.claude_code_timeout_seconds,
        )

    if provider == "codex":
        return CodexAdapter(
            cli_path=settings.codex_cli_path or None,
            timeout_seconds=settings.codex_timeout_seconds,
        )

    if provider == "antigravity":
        return AntigravityAdapter(
            cli_path=settings.antigravity_cli_path or None,
            prompt_flag=settings.antigravity_prompt_flag,
            timeout_seconds=settings.antigravity_timeout_seconds,
        )

    if provider == "local":
        return LocalLLMAdapter(
            base_url=settings.local_llm_base_url,
            model=settings.local_llm_model,
            api_key=settings.local_llm_api_key,
            timeout_seconds=settings.local_llm_timeout_seconds,
        )

    if provider == "ollama":
        return OllamaAdapter(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        )

    if provider == "mock":
        return MockLLMAdapter()

    # 課金 API（非推奨パス）
    if provider == "claude":
        from .claude_adapter import ClaudeAdapter

        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY は LLM_PROVIDER=claude の場合に必須です。"
                "（非推奨）課金 API を使用しないために LLM_PROVIDER=claude-code に "
                "変更してください。"
            )
        return ClaudeAdapter(api_key=settings.anthropic_api_key)

    raise ValueError(
        f"未知の LLM_PROVIDER: '{provider}'。対応プロバイダ: {', '.join(_SUPPORTED_PROVIDERS)}"
    )

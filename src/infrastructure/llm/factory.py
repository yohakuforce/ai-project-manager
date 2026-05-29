"""
LLM アダプタのファクトリ。
環境変数 LLM_PROVIDER に基づいてアダプタインスタンスを生成する。

2026-05-15 更新:
  - claude-code (ClaudeCodeAdapter) を追加・デフォルト化
  - ollama / codex / antigravity のスタブを追加
  - ClaudeAdapter（課金 API）は非推奨
"""

from __future__ import annotations

from src.config import get_settings

from .adapter import LLMAdapter
from .claude_code_adapter import ClaudeCodeAdapter
from .mock_adapter import MockLLMAdapter

# 課金 API アダプタは遅延インポートで非推奨警告を出す
# from .claude_adapter import ClaudeAdapter  # DEPRECATED

_SUPPORTED_PROVIDERS = ("claude-code", "codex", "antigravity", "ollama", "mock")


def create_llm_adapter() -> LLMAdapter:
    """
    LLM_PROVIDER 環境変数に基づいてアダプタを生成する。

    対応プロバイダ:
      - claude-code  : Claude Code CLI（subprocess 経由）。サブスク AI。デフォルト。
      - codex        : Codex CLI（subprocess 経由）。サブスク AI。（スタブ実装）
      - antigravity  : Antigravity CLI 経由。サブスク AI。（スタブ実装）
      - ollama       : ローカル Ollama サーバ。完全オフライン。（スタブ実装）
      - mock         : テスト用モック。

    非推奨（削除予定）:
      - claude       : Anthropic API 直接呼び出し（課金 API）。本番使用禁止。
    """
    settings = get_settings()
    provider = settings.llm_provider.lower()

    if provider == "claude-code":
        return ClaudeCodeAdapter()

    if provider == "mock":
        return MockLLMAdapter()

    # --- スタブ実装（将来: CLI ラッパーを実装） ---
    if provider == "codex":
        # TODO: CodexAdapter 実装（Codex CLI 経由）
        raise NotImplementedError(
            "CodexAdapter は未実装です。LLM_PROVIDER=claude-code を使用してください。"
        )

    if provider == "antigravity":
        # TODO: AntigravityAdapter 実装
        raise NotImplementedError(
            "AntigravityAdapter は未実装です。LLM_PROVIDER=claude-code を使用してください。"
        )

    if provider == "ollama":
        # TODO: OllamaAdapter 実装（ローカル HTTP 呼び出し）
        raise NotImplementedError(
            "OllamaAdapter は未実装です。LLM_PROVIDER=claude-code を使用してください。"
        )

    # 課金 API（非推奨パス）
    if provider == "claude":
        from .claude_adapter import ClaudeAdapter

        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY は LLM_PROVIDER=claude の場合に必須です。"
                "（非推奨）課金 API を使用しないために LLM_PROVIDER=claude-code に変更してください。"
            )
        return ClaudeAdapter(api_key=settings.anthropic_api_key)

    raise ValueError(
        f"未知の LLM_PROVIDER: '{provider}'。対応プロバイダ: {', '.join(_SUPPORTED_PROVIDERS)}"
    )

"""LocalLLMAdapter — OpenAI 互換 HTTP API でローカル LLM を呼ぶ（LLM_PROVIDER=local）。

特定の実装に縛られない: `/v1/chat/completions`（OpenAI 互換）を公開するあらゆる
ローカルサーバで動作する — Ollama（http://localhost:11434/v1）、LM Studio
（http://localhost:1234/v1）、vLLM、llama.cpp server、text-generation-webui など。

設定:
  LOCAL_LLM_BASE_URL  — OpenAI 互換のベースURL（既定 http://localhost:11434/v1）
  LOCAL_LLM_MODEL     — モデル名（既定 llama3）
  LOCAL_LLM_API_KEY   — 任意（鍵を要求するサーバ向け。ローカルは通常空）
"""

from __future__ import annotations

from typing import Any

import httpx

from .adapter import LLMAdapter, LLMResponse


class LocalLLMAdapter:
    """OpenAI 互換エンドポイント経由でローカル LLM を呼ぶアダプタ。"""

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "llama3",
        api_key: str = "",
        timeout_seconds: int = 180,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds

    @property
    def model_name(self) -> str:
        return f"local:{self._model}"

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> LLMResponse:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
            except httpx.ConnectError as exc:
                raise RuntimeError(
                    f"ローカル LLM に接続できません（{self._base_url}）。"
                    "サーバが起動しているか・URL が正しいか確認してください。"
                ) from exc

        if response.status_code != 200:
            raise RuntimeError(
                f"ローカル LLM が HTTP {response.status_code} を返しました: {response.text[:200]}"
            )

        data = response.json()
        content = _extract_content(data)
        usage = data.get("usage") or {}
        return LLMResponse(
            content=content.strip(),
            model=self._model,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
        )


def _extract_content(data: dict[str, Any]) -> str:
    """OpenAI 互換応答から本文を取り出す。"""
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"ローカル LLM の応答を解釈できません: {str(data)[:200]}") from exc
    if not content:
        raise RuntimeError("ローカル LLM が空の応答を返しました。")
    return str(content)


# isinstance チェックで LLMAdapter プロトコルを満たすことを確認
_: LLMAdapter = LocalLLMAdapter.__new__(LocalLLMAdapter)  # type: ignore[assignment]

"""OllamaAdapter — ローカル Ollama サーバを native API で呼ぶ（LLM_PROVIDER=ollama）。

Ollama 固有の /api/generate を使う。Ollama 以外のローカルサーバを使いたい場合や
OpenAI 互換エンドポイントで統一したい場合は LLM_PROVIDER=local（LocalLLMAdapter）を
使う。

設定: OLLAMA_BASE_URL（既定 http://localhost:11434）/ OLLAMA_MODEL（既定 llama3）。
"""

from __future__ import annotations

from typing import Any

import httpx

from .adapter import LLMAdapter, LLMResponse


class OllamaAdapter:
    """ローカル Ollama サーバ（HTTP /api/generate）の LLM アダプタ。"""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3",
        timeout_seconds: int = 180,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds

    @property
    def model_name(self) -> str:
        return f"ollama:{self._model}"

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(f"{self._base_url}/api/generate", json=payload)
            except httpx.ConnectError as exc:
                raise RuntimeError(
                    f"Ollama に接続できません（{self._base_url}）。"
                    "`ollama serve` で起動しているか確認してください。"
                ) from exc

        if response.status_code != 200:
            raise RuntimeError(
                f"Ollama が HTTP {response.status_code} を返しました: {response.text[:200]}"
            )

        data = response.json()
        content = data.get("response", "")
        if not content:
            raise RuntimeError("Ollama が空の応答を返しました。")
        return LLMResponse(
            content=str(content).strip(),
            model=self._model,
            prompt_tokens=int(data.get("prompt_eval_count", 0)),
            completion_tokens=int(data.get("eval_count", 0)),
        )


# isinstance チェックで LLMAdapter プロトコルを満たすことを確認
_: LLMAdapter = OllamaAdapter.__new__(OllamaAdapter)  # type: ignore[assignment]

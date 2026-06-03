"""共有: CLI ベースのサブスク LLM アダプタ（claude-code / codex / antigravity）の
subprocess 実行・タイムアウト・エラー処理を一元化する。

各アダプタは「実行コマンド」と「出力の解釈」だけを宣言すればよい。いずれも課金
API ではなく、ローカルに認証済みインストールされた CLI を subprocess 経由で
呼び出す（サブスクリプション範囲・ゼロ単価）。
"""

from __future__ import annotations

import asyncio
import json
import shutil

# トークン ≒ 文字数 / 4（CLI はトークン数を返さないため概算）
_TOKEN_CHARS_RATIO = 4


def resolve_cli(cli_path: str, binary: str, not_found_hint: str) -> str:
    """明示パスがあればそれを、なければ PATH から binary を解決する。

    Raises:
        RuntimeError: バイナリが見つからない場合。
    """
    if cli_path:
        return cli_path
    found = shutil.which(binary)
    if found is None:
        raise RuntimeError(not_found_hint)
    return found


async def run_cli(
    cmd: list[str],
    timeout_seconds: int,
    label: str,
    not_found_hint: str,
) -> str:
    """CLI コマンドを実行し stdout テキストを返す。

    Raises:
        RuntimeError: 非ゼロ終了 / バイナリ不在 / タイムアウト。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        raise RuntimeError(f"{label} がタイムアウトしました（{timeout_seconds}秒）。") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(not_found_hint) from exc

    if proc.returncode != 0:
        err_text = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"{label} がエラーを返しました（終了コード {proc.returncode}）: {err_text}"
        )

    return stdout.decode("utf-8", errors="replace").strip()


def parse_cli_content(raw_output: str) -> str:
    """CLI の stdout からテキストを取り出す（JSON {result|content} or 生テキスト）。"""
    if not raw_output:
        return ""
    try:
        data = json.loads(raw_output)
    except (json.JSONDecodeError, ValueError):
        return raw_output
    if isinstance(data, dict):
        return str(data.get("result") or data.get("content") or raw_output)
    return raw_output


def estimate_tokens(text: str) -> int:
    """文字数からトークン数を概算する（最低 1）。"""
    return max(1, len(text) // _TOKEN_CHARS_RATIO)

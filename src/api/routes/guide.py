"""
運用ガイド表示ルーター（GET /guide）。

docs/operations-guide.md を読み込み、ブランド統一した HTML に変換して返す。
認証不要（localhost 専用の運用ドキュメント）。Markdown は本リポジトリが管理する
限定的な記法（見出し / 箇条書き / 番号付き / 表 / コードブロック / 強調 / リンク）
のみを使うため、軽量な内製コンバータで十分。
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["guide"])

# src/api/routes/guide.py -> リポジトリルート/docs/operations-guide.md
_GUIDE_PATH = Path(__file__).resolve().parents[3] / "docs" / "operations-guide.md"

_HEAD = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI-Project-Manager 運用ガイド</title>
<style>
  :root{
    --ink:#0b0b0c;--ink-soft:#2a2a2e;--paper:#fbfaf8;--paper-2:#f2efea;--line:#e4ded5;
    --crimson:#b51b2e;--crimson-d:#8c1322;--muted:#6f6a62;
    --serif:"Hiragino Mincho ProN","Yu Mincho","Noto Serif JP",Georgia,serif;
    --sans:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP","Segoe UI",sans-serif;
    --mono:"SF Mono","JetBrains Mono","Roboto Mono",monospace;
  }
  *{box-sizing:border-box;}
  html{scroll-behavior:smooth;}
  body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.85;}
  .bar{position:sticky;top:0;z-index:10;background:rgba(251,250,248,.93);backdrop-filter:blur(10px);
    border-bottom:1px solid var(--line);}
  .bar .inner{max-width:820px;margin:0 auto;padding:13px 22px;display:flex;gap:16px;align-items:baseline;}
  .bar .mark{font-family:var(--serif);font-weight:600;font-size:1.05rem;padding-left:13px;position:relative;}
  .bar .mark::before{content:"";position:absolute;left:0;top:2px;bottom:2px;width:4px;background:var(--crimson);}
  .bar a{margin-left:auto;font-size:.82rem;color:var(--crimson-d);text-decoration:none;border-bottom:1px solid var(--line);}
  .bar a:hover{border-color:var(--crimson);}
  main{max-width:820px;margin:0 auto;padding:14px 22px 100px;}
  h1{font-family:var(--serif);font-weight:600;font-size:1.7rem;margin:24px 0 10px;line-height:1.3;}
  h2{font-family:var(--serif);font-weight:600;font-size:1.25rem;margin:34px 0 8px;
    padding-top:14px;border-top:1px solid var(--line);}
  h3{font-size:1.0rem;margin:22px 0 6px;}
  p{margin:10px 0;}
  ul,ol{margin:10px 0;padding-left:22px;}
  li{margin:5px 0;}
  a{color:var(--crimson-d);}
  strong{color:var(--ink);}
  hr{border:0;border-top:1px solid var(--line);margin:26px 0;}
  blockquote{margin:14px 0;padding:8px 16px;border-left:3px solid var(--crimson);background:#fff;
    color:var(--ink-soft);font-size:.92rem;}
  code{font-family:var(--mono);font-size:.86em;background:var(--paper-2);padding:1px 6px;border-radius:3px;
    color:var(--crimson-d);}
  pre{background:var(--ink);color:#ededed;border-radius:3px;padding:14px 16px;overflow-x:auto;
    font-family:var(--mono);font-size:.82rem;line-height:1.7;margin:12px 0;}
  pre code{background:none;color:inherit;padding:0;}
  table{width:100%;border-collapse:collapse;margin:14px 0;font-size:.86rem;}
  th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top;}
  th{font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);}
  td code{white-space:nowrap;}
</style>
</head>
<body>
<div class="bar"><div class="inner">
  <span class="mark">AI-Project-Manager 運用ガイド</span>
  <a href="/register">プロジェクト/メンバー登録 →</a>
  <a href="/settings">設定画面へ →</a>
</div></div>
<main>
"""

_TAIL = "</main></body></html>"


def _inline(text: str) -> str:
    """インライン記法（エスケープ → コード・リンク・強調）を変換する。"""
    out = html.escape(text)
    # `code`
    out = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", out)
    # [text](url)
    out = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        out,
    )
    # **bold**
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return out


def _render_table(rows: list[str]) -> str:
    """Markdown のパイプ表を HTML テーブルに変換する（2 行目は区切り）。"""

    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    header = cells(rows[0])
    body = [cells(r) for r in rows[2:]]
    thead = "".join(f"<th>{_inline(c)}</th>" for c in header)
    trs = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{trs}</tbody></table>"


def _md_to_html(md: str) -> str:
    """限定 Markdown を HTML へ変換する。"""
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    list_mode: str | None = None  # "ul" | "ol" | None

    def close_list() -> None:
        nonlocal list_mode
        if list_mode:
            out.append(f"</{list_mode}>")
            list_mode = None

    while i < n:
        line = lines[i]

        # コードブロック
        if line.startswith("```"):
            close_list()
            i += 1
            buf: list[str] = []
            while i < n and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # 閉じる ```
            code = html.escape("\n".join(buf))
            out.append(f"<pre><code>{code}</code></pre>")
            continue

        # 表（| ... | が連続）
        if line.lstrip().startswith("|") and i + 1 < n and set(lines[i + 1].strip()) <= set("|-: "):
            close_list()
            tbl = [line]
            i += 1
            while i < n and lines[i].lstrip().startswith("|"):
                tbl.append(lines[i])
                i += 1
            out.append(_render_table(tbl))
            continue

        stripped = line.strip()

        if not stripped:
            close_list()
            i += 1
            continue

        if stripped == "---":
            close_list()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith("### "):
            close_list()
            out.append(f"<h3>{_inline(stripped[4:])}</h3>")
            i += 1
            continue
        if stripped.startswith("## "):
            close_list()
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
            i += 1
            continue
        if stripped.startswith("# "):
            close_list()
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
            i += 1
            continue

        if stripped.startswith("> "):
            close_list()
            out.append(f"<blockquote>{_inline(stripped[2:])}</blockquote>")
            i += 1
            continue

        # 番号付きリスト
        m = re.match(r"^\d+\.\s+(.*)$", stripped)
        if m:
            if list_mode != "ol":
                close_list()
                out.append("<ol>")
                list_mode = "ol"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            i += 1
            continue

        # 箇条書き
        if stripped.startswith("- "):
            if list_mode != "ul":
                close_list()
                out.append("<ul>")
                list_mode = "ul"
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            i += 1
            continue

        # 段落
        close_list()
        out.append(f"<p>{_inline(stripped)}</p>")
        i += 1

    close_list()
    return "\n".join(out)


def _render_guide_page() -> str:
    try:
        md = _GUIDE_PATH.read_text(encoding="utf-8")
        body = _md_to_html(md)
    except OSError:
        body = (
            "<p>運用ガイド（docs/operations-guide.md）が見つかりませんでした。"
            "リポジトリ同梱のドキュメントを参照してください。</p>"
        )
    return _HEAD + body + _TAIL


@router.get("/guide", response_class=HTMLResponse)
async def get_guide() -> Response:
    """運用ガイドを HTML で返す（localhost 運用ドキュメント）。"""
    return HTMLResponse(content=_render_guide_page())

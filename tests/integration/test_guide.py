"""運用ガイド（GET /guide）と Markdown コンバータのテスト。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.routes.guide import _md_to_html


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


class TestGuidePage:
    def test_returns_html(self, client: TestClient) -> None:
        res = client.get("/guide")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    def test_no_api_key_required(self, client: TestClient) -> None:
        # 認証不要（localhost 運用ドキュメント）
        assert client.get("/guide").status_code == 200

    def test_contains_key_operational_content(self, client: TestClient) -> None:
        body = client.get("/guide").text
        # context_hub_api_key の取得方法（自分で決めて両者一致）
        assert "DEV_API_KEY" in body
        # AI-PM API 認証
        assert "X-Api-Key" in body
        # 主要セクションが描画されている
        assert "プロジェクトとメンバーを登録" in body
        assert "pipeline/wrap-up" in body
        # 表・コードが HTML 化されている
        assert "<table>" in body
        assert "<pre><code>" in body
        # 設定画面への導線
        assert 'href="/settings"' in body

    def test_settings_links_to_guide(self, client: TestClient) -> None:
        assert 'href="/guide"' in client.get("/settings").text


class TestMarkdownConverter:
    def test_heading_and_paragraph(self) -> None:
        out = _md_to_html("## 見出し\n\n本文です。")
        assert "<h2>見出し</h2>" in out
        assert "<p>本文です。</p>" in out

    def test_unordered_and_ordered_lists(self) -> None:
        assert "<ul>" in _md_to_html("- a\n- b")
        assert "<ol>" in _md_to_html("1. a\n2. b")

    def test_code_block_is_escaped(self) -> None:
        out = _md_to_html("```\n<script>x</script>\n```")
        assert "<pre><code>" in out
        assert "&lt;script&gt;" in out

    def test_inline_code_bold_and_link(self) -> None:
        out = _md_to_html("`code` と **強調** と [リンク](https://example.com)")
        assert "<code>code</code>" in out
        assert "<strong>強調</strong>" in out
        assert '<a href="https://example.com"' in out

    def test_table(self) -> None:
        out = _md_to_html("| A | B |\n|---|---|\n| 1 | 2 |")
        assert "<table>" in out
        assert "<th>A</th>" in out
        assert "<td>1</td>" in out

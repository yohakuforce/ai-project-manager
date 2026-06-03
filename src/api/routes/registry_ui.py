"""
プロジェクト・メンバー登録 GUI ルーター（/register）。

localhost 専用・認証不要（/settings と同じ運用前提）。サーバーレンダリングのフォームで
プロジェクトとメンバーを作成・一覧する。RegistryService（DI）を直接使うため、
登録内容はそのまま同プロセスのアプリ（スケジューラ・各 API）から見える。
"""

from __future__ import annotations

import html
import logging
from typing import Annotated

from fastapi import APIRouter, Form, Response
from fastapi.responses import HTMLResponse

from src.api.deps import get_registry_service
from src.application.registry.service import (
    MemberView,
    ProjectView,
    RegistryError,
)
from src.config.settings import get_settings
from src.domain.member.value_objects import MemberRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/register", tags=["registry-ui"])

_HEAD = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI-Project-Manager 登録（プロジェクト / メンバー）</title>
<style>
  :root{
    --ink:#0b0b0c;--ink-soft:#2a2a2e;--paper:#fbfaf8;--paper-2:#f2efea;--line:#e4ded5;
    --crimson:#b51b2e;--crimson-d:#8c1322;--muted:#6f6a62;--ok:#0a7a3d;
    --serif:"Hiragino Mincho ProN","Yu Mincho","Noto Serif JP",Georgia,serif;
    --sans:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP","Segoe UI",sans-serif;
    --mono:"SF Mono","JetBrains Mono","Roboto Mono",monospace;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.7;}
  .bar{position:sticky;top:0;z-index:10;background:rgba(251,250,248,.93);backdrop-filter:blur(10px);
    border-bottom:1px solid var(--line);}
  .bar .inner{max-width:880px;margin:0 auto;padding:13px 22px;display:flex;gap:16px;align-items:baseline;}
  .bar .mark{font-family:var(--serif);font-weight:600;font-size:1.05rem;padding-left:13px;position:relative;}
  .bar .mark::before{content:"";position:absolute;left:0;top:2px;bottom:2px;width:4px;background:var(--crimson);}
  .bar .links{margin-left:auto;display:flex;gap:14px;}
  .bar a{font-size:.82rem;color:var(--crimson-d);text-decoration:none;border-bottom:1px solid var(--line);}
  .bar a:hover{border-color:var(--crimson);}
  main{max-width:880px;margin:0 auto;padding:16px 22px 90px;}
  h2{font-family:var(--serif);font-weight:600;font-size:1.2rem;margin:30px 0 10px;}
  p.lead{font-size:.84rem;color:var(--muted);margin:6px 0 14px;}
  fieldset{border:1px solid var(--line);border-radius:3px;padding:8px 18px 16px;margin:14px 0;background:#fff;}
  legend{font-family:var(--serif);font-weight:600;font-size:1rem;padding:0 8px;}
  label{display:block;font-size:.82rem;font-weight:600;margin:12px 0 4px;}
  input[type=text],select{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:3px;
    font-size:.9rem;background:var(--paper);font-family:var(--sans);}
  input:focus,select:focus{outline:none;border-color:var(--ink);background:#fff;}
  .hint{font-size:.74rem;color:var(--muted);margin:4px 0 0;}
  .row{display:flex;gap:14px;flex-wrap:wrap;}
  .row>div{flex:1 1 200px;}
  button.save{margin-top:14px;background:var(--ink);color:#fff;border:0;padding:9px 24px;border-radius:3px;
    font-size:.86rem;cursor:pointer;}
  button.save:hover{background:var(--crimson-d);}
  table{width:100%;border-collapse:collapse;margin:10px 0;font-size:.84rem;}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top;}
  th{font-size:.7rem;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);}
  td.uuid{font-family:var(--mono);font-size:.76rem;color:var(--ink-soft);white-space:nowrap;}
  .empty{color:var(--muted);font-size:.84rem;padding:10px 0;}
  .banner-ok,.banner-err{background:#fff;border:1px solid var(--line);border-left:3px solid var(--crimson);
    padding:11px 15px;border-radius:3px;margin:14px 0;font-size:.88rem;}
  .banner-err{color:var(--crimson-d);}
  code{font-family:var(--mono);font-size:.86em;background:var(--paper-2);padding:1px 5px;border-radius:3px;color:var(--crimson-d);}
  .delform{margin:0;}
  button.del{background:#fff;color:var(--crimson-d);border:1px solid var(--line);padding:4px 12px;
    border-radius:3px;font-size:.76rem;cursor:pointer;}
  button.del:hover{border-color:var(--crimson);background:#fff5f5;}
</style>
</head>
<body>
<div class="bar"><div class="inner">
  <span class="mark">プロジェクト / メンバー登録</span>
  <span class="links"><a href="/settings">設定</a><a href="/guide">運用ガイド</a></span>
</div></div>
<main>
"""

_TAIL = "</main></body></html>"


def _role_options(selected: str = "developer") -> str:
    return "".join(
        f'<option value="{r.value}" {"selected" if r.value == selected else ""}>{r.value}</option>'
        for r in MemberRole
    )


def _delete_form(action: str, label: str) -> str:
    """確認ダイアログ付きのインライン削除ボタン。"""
    return (
        f'<form method="POST" action="{action}" class="delform" '
        f"onsubmit=\"return confirm('{label}を削除します。よろしいですか？')\">"
        '<button type="submit" class="del">削除</button></form>'
    )


def _project_rows(projects: list[ProjectView]) -> str:
    if not projects:
        return (
            '<p class="empty">まだプロジェクトがありません。上のフォームから登録してください。</p>'
        )
    head = (
        "<table><thead><tr><th>名前</th><th>顧客</th><th>ゴール</th>"
        "<th>Context-Hub PJ</th><th>タスク</th><th>プロジェクトUUID</th><th></th></tr></thead><tbody>"
    )
    body = "".join(
        f"<tr><td>{html.escape(p.name)}</td><td>{html.escape(p.customer)}</td>"
        f"<td>{html.escape(p.goal)}</td><td>{html.escape(p.context_hub_project_id)}</td>"
        f"<td>{p.task_count}</td><td class='uuid'>{html.escape(p.project_id)}</td>"
        f"<td>{_delete_form(f'/register/project/{p.project_id}/delete', 'プロジェクト「' + html.escape(p.name) + '」')}</td></tr>"
        for p in projects
    )
    return head + body + "</tbody></table>"


def _member_rows(members: list[MemberView]) -> str:
    if not members:
        return '<p class="empty">まだメンバーがいません。上のフォームから登録してください。</p>'
    head = (
        "<table><thead><tr><th>名前</th><th>external_id</th><th>役割</th>"
        "<th>メンバーUUID</th><th></th></tr></thead><tbody>"
    )
    body = "".join(
        f"<tr><td>{html.escape(m.name)}</td><td><code>{html.escape(m.external_id)}</code></td>"
        f"<td>{html.escape(m.role)}</td><td class='uuid'>{html.escape(m.member_id)}</td>"
        f"<td>{_delete_form(f'/register/member/{m.member_id}/delete', 'メンバー「' + html.escape(m.name) + '」')}</td></tr>"
        for m in members
    )
    return head + body + "</tbody></table>"


def _render_page(
    projects: list[ProjectView],
    members: list[MemberView],
    default_endpoint: str,
    banner: str = "",
) -> str:
    ep = html.escape(default_endpoint)
    return (
        _HEAD
        + banner
        + "<h2>プロジェクトを登録</h2>"
        + '<p class="lead">登録すると即座にこのアプリ（スケジューラ・各 API）から利用できます。'
        "出力された<b>プロジェクトUUID</b>を API 呼び出しの project_id に使います。</p>"
        + '<form method="POST" action="/register/project"><fieldset><legend>新規プロジェクト</legend>'
        + '<label for="name">プロジェクト名 *</label>'
        '<input type="text" id="name" name="name" placeholder="例: 案件A 基幹刷新">'
        + '<div class="row"><div><label for="customer">顧客名</label>'
        '<input type="text" id="customer" name="customer" placeholder="例: 顧客A"></div>'
        '<div><label for="goal">ゴール</label>'
        '<input type="text" id="goal" name="goal" placeholder="例: 認証基盤の刷新を完了する"></div></div>'
        + '<div class="row"><div><label for="context_hub_project_id">Context-Hub プロジェクトID</label>'
        '<input type="text" id="context_hub_project_id" name="context_hub_project_id" placeholder="例: proj-001（連携しないなら空でOK）">'
        '<p class="hint">Context-Hub と連携する場合のみ。会議/課題の取得元プロジェクトの ID。</p></div>'
        f'<div><label for="api_endpoint">Context-Hub エンドポイント</label>'
        f'<input type="text" id="api_endpoint" name="api_endpoint" value="{ep}"></div></div>'
        + '<button type="submit" class="save">プロジェクトを登録</button></fieldset></form>'
        + "<h2>登録済みプロジェクト</h2>"
        + _project_rows(projects)
        + "<h2>メンバーを登録</h2>"
        + '<p class="lead">割当・日報・通知の対象になります。external_id は Slack のユーザーID/チャンネルとして'
        "通知の宛先解決にも使われます。</p>"
        + '<form method="POST" action="/register/member"><fieldset><legend>新規メンバー</legend>'
        + '<div class="row"><div><label for="external_id">external_id *</label>'
        '<input type="text" id="external_id" name="external_id" placeholder="例: U01234567 または user-a">'
        '<p class="hint">Slack のユーザーID（DM 送信先）など。重複不可。</p></div>'
        '<div><label for="member_name">名前 *</label>'
        '<input type="text" id="member_name" name="name" placeholder="例: 山田 太郎"></div>'
        '<div><label for="role">役割</label>'
        f'<select id="role" name="role">{_role_options()}</select></div></div>'
        + '<button type="submit" class="save">メンバーを登録</button></fieldset></form>'
        + "<h2>登録済みメンバー</h2>"
        + _member_rows(members)
        + _TAIL
    )


async def _render(banner: str = "") -> HTMLResponse:
    service = get_registry_service()
    projects = await service.list_projects()
    members = await service.list_members()
    endpoint = get_settings().context_hub_base_url
    return HTMLResponse(content=_render_page(projects, members, endpoint, banner))


@router.get("", response_class=HTMLResponse)
async def get_register_page() -> Response:
    return await _render()


@router.post("/project", response_class=HTMLResponse)
async def post_project(
    name: Annotated[str, Form()] = "",
    customer: Annotated[str, Form()] = "",
    goal: Annotated[str, Form()] = "",
    context_hub_project_id: Annotated[str, Form()] = "",
    api_endpoint: Annotated[str, Form()] = "",
) -> Response:
    service = get_registry_service()
    try:
        view = await service.create_project(
            name=name,
            customer=customer,
            goal=goal,
            context_hub_project_id=context_hub_project_id,
            api_endpoint=api_endpoint or get_settings().context_hub_base_url,
        )
    except RegistryError as exc:
        return await _render(f'<div class="banner-err">登録エラー: {html.escape(str(exc))}</div>')
    banner = (
        '<div class="banner-ok">プロジェクト「'
        + html.escape(view.name)
        + "」を登録しました。UUID: <code>"
        + html.escape(view.project_id)
        + "</code></div>"
    )
    return await _render(banner)


@router.post("/member", response_class=HTMLResponse)
async def post_member(
    external_id: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "developer",
) -> Response:
    service = get_registry_service()
    try:
        view = await service.create_member(external_id=external_id, name=name, role=role)
    except RegistryError as exc:
        return await _render(f'<div class="banner-err">登録エラー: {html.escape(str(exc))}</div>')
    banner = (
        '<div class="banner-ok">メンバー「' + html.escape(view.name) + "」を登録しました。</div>"
    )
    return await _render(banner)


@router.post("/project/{project_id}/delete", response_class=HTMLResponse)
async def delete_project(project_id: str) -> Response:
    service = get_registry_service()
    try:
        name = await service.delete_project(project_id)
    except RegistryError as exc:
        return await _render(f'<div class="banner-err">削除エラー: {html.escape(str(exc))}</div>')
    return await _render(
        f'<div class="banner-ok">プロジェクト「{html.escape(name)}」を削除しました。</div>'
    )


@router.post("/member/{member_id}/delete", response_class=HTMLResponse)
async def delete_member(member_id: str) -> Response:
    service = get_registry_service()
    try:
        name = await service.delete_member(member_id)
    except RegistryError as exc:
        return await _render(f'<div class="banner-err">削除エラー: {html.escape(str(exc))}</div>')
    return await _render(
        f'<div class="banner-ok">メンバー「{html.escape(name)}」を削除しました。</div>'
    )

"""
プロジェクト＋メンバーを登録する seed スクリプト。

AI-PM には現状プロジェクト/メンバー登録用の GUI/API が無いため、本スクリプトで投入する。
保存先は設定（USE_DATABASE）に従う:
  - USE_DATABASE=true  → PostgreSQL に永続化（同じ DB を見るアプリから利用可能）★推奨
  - USE_DATABASE=false → インメモリ（プロセス内のみ。別プロセスのアプリからは見えない）

使い方:
    USE_DATABASE=true .venv/bin/python scripts/seed_project.py \
        --name "案件A" --customer "顧客A" --goal "認証基盤の刷新" \
        --member "user-a:山田" --member "user-b:鈴木" \
        --context-hub-project proj-001

出力されたプロジェクト UUID を、以降の API 呼び出し（/api/v1/...）の project_id に使う。
"""

from __future__ import annotations

import argparse
import asyncio

from src.config.settings import get_settings
from src.domain.member.aggregate import Member
from src.domain.member.value_objects import MemberId, MemberRole
from src.domain.project.aggregate import Project
from src.domain.project.value_objects import ContextHubProjectRef, ProjectId
from src.infrastructure.repositories.factory import build_repositories


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="プロジェクト＋メンバーを登録する")
    p.add_argument("--name", required=True, help="プロジェクト名")
    p.add_argument("--customer", default="（未設定）", help="顧客名")
    p.add_argument("--goal", default="（未設定）", help="プロジェクトのゴール")
    p.add_argument(
        "--member",
        action="append",
        default=[],
        metavar="EXTERNAL_ID:名前[:role]",
        help="メンバー（複数指定可）。例: user-a:山田 や user-b:鈴木:pl",
    )
    p.add_argument(
        "--context-hub-project",
        default="",
        help="Context-Hub 側のプロジェクト ID（連携しない場合は任意の文字列で可）",
    )
    p.add_argument(
        "--context-hub-endpoint",
        default="",
        help="Context-Hub API エンドポイント（既定は設定の context_hub_base_url）",
    )
    return p.parse_args()


def _build_member(spec: str) -> Member:
    parts = spec.split(":")
    external_id = parts[0].strip()
    name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else external_id
    role_str = parts[2].strip().lower() if len(parts) > 2 else "developer"
    try:
        role = MemberRole(role_str)
    except ValueError:
        role = MemberRole.DEVELOPER
    return Member(
        member_id=MemberId.generate(),
        external_id=external_id,
        name=name,
        role=role,
    )


async def _seed(args: argparse.Namespace) -> None:
    settings = get_settings()
    repos = build_repositories(settings)

    if not settings.use_database:
        print(
            "⚠️  USE_DATABASE=false（インメモリ）です。本スクリプトの登録内容は\n"
            "    別プロセスで動くアプリからは見えません。永続化するには USE_DATABASE=true で実行してください。"
        )

    project = Project(
        project_id=ProjectId.generate(),
        name=args.name,
        customer=args.customer,
        goal=args.goal,
        context_hub_ref=ContextHubProjectRef(
            context_hub_project_id=args.context_hub_project or "(none)",
            api_endpoint=args.context_hub_endpoint or settings.context_hub_base_url,
        ),
    )
    await repos.project.save(project)

    members = [_build_member(s) for s in args.member]
    for m in members:
        await repos.member.save(m)

    if repos.engine is not None:
        await repos.engine.dispose()

    print("✅ 登録しました。")
    print(f"  プロジェクトUUID : {project.project_id}")
    print(f"  プロジェクト名   : {project.name}")
    print(f"  Context-Hub PJ   : {project.context_hub_ref.context_hub_project_id}")
    if members:
        print("  メンバー:")
        for m in members:
            print(
                f"    - {m.name}（external_id={m.external_id} / role={m.role.value} / id={m.member_id}）"
            )
    print()
    print("次のステップ: このプロジェクトUUIDを project_id にして /api/v1/... を呼び出します。")
    print("  詳しくは docs/operations-guide.md（または起動中アプリの /guide）§6 以降。")


def main() -> None:
    asyncio.run(_seed(_parse_args()))


if __name__ == "__main__":
    main()

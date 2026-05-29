"""
Context-Hub モッククライアント。
CONTEXT_HUB_USE_MOCK=true の場合に使用する。
Context-Hub 本体なしで AI-PM を先行開発・テストするために使う。
"""

from __future__ import annotations

from .client import (
    IssueAssignee,
    IssueResponse,
    MeetingExtractedTask,
    MeetingResponse,
    MemberResponse,
    ProjectContextResponse,
    SearchResult,
)


class MockContextHubClient:
    """
    Context-Hub API のモック実装。
    固定のサンプルデータ（マスキング済み）を返す。
    テスト時は monkey-patch で返却値をカスタマイズできる。
    """

    async def get_project_context(
        self,
        context_hub_project_id: str,
        context_type: str = "overview",
    ) -> ProjectContextResponse:
        return ProjectContextResponse(
            project_id=context_hub_project_id,
            name="[MOCK] サンプルプロジェクト",
            summary="[MOCK] プロジェクトのコンテキストサマリ。テスト用データです。",
            active_sources=["slack", "backlog"],
            document_count=42,
            issue_count=15,
        )

    async def get_members(
        self,
        context_hub_project_id: str,
    ) -> list[MemberResponse]:
        return [
            MemberResponse(
                external_id="member-001",
                name="[MOCK] 田中 太郎",
                sources=["backlog"],
                assigned_issue_count=3,
                last_activity_at="2026-05-14T18:00:00Z",
            ),
            MemberResponse(
                external_id="member-002",
                name="[MOCK] 鈴木 花子",
                sources=["backlog", "redmine"],
                assigned_issue_count=5,
                last_activity_at="2026-05-14T17:30:00Z",
            ),
        ]

    async def get_meeting(
        self,
        context_hub_project_id: str,
        meeting_id: str,
    ) -> MeetingResponse:
        return MeetingResponse(
            meeting_id=meeting_id,
            title="[MOCK] 週次進捗会議",
            meeting_at="2026-05-14T10:00:00Z",
            participants=["[MOCK] 田中 太郎", "[MOCK] 鈴木 花子"],
            raw_transcript="[MOCK] 会議書き起こしテキスト。テスト用データです。",
            summary="[MOCK] API 仕様を 5/20 までに確定することで合意。",
            decisions=["API 仕様を 5/20 までに確定"],
            extracted_tasks=[
                MeetingExtractedTask(
                    title="[MOCK] API 仕様ドラフト作成",
                    suggested_assignee="[MOCK] 田中 太郎",
                    suggested_due_date="2026-05-20",
                ),
                MeetingExtractedTask(
                    title="[MOCK] テスト環境セットアップ",
                    suggested_assignee="[MOCK] 鈴木 花子",
                    suggested_due_date="2026-05-18",
                ),
            ],
        )

    async def get_issues(
        self,
        context_hub_project_id: str,
        source: str,
        status: str = "open",
        updated_since: str | None = None,
        limit: int = 50,
    ) -> list[IssueResponse]:
        return [
            IssueResponse(
                issue_id="issue-001",
                source_type=source,
                external_id="42",
                title="[MOCK] ログイン画面のバリデーション修正",
                description="[MOCK] テスト用 Issue データ。",
                status="in_progress",
                priority="high",
                due_date="2026-05-20",
                assignee=IssueAssignee(external_id="member-001", name="[MOCK] 田中 太郎"),
                labels=["フロントエンド", "バグ"],
                comment_count=3,
                created_at="2026-05-10T09:00:00Z",
                updated_at="2026-05-14T18:00:00Z",
            ),
        ]

    async def search(
        self,
        context_hub_project_id: str,
        query: str,
        top_k: int = 5,
        source_types: list[str] | None = None,
    ) -> list[SearchResult]:
        return [
            SearchResult(
                document_id="doc-001",
                source_type="meeting",
                title="[MOCK] 設計レビュー会議",
                snippet="[MOCK] JWT を使った認証方式で合意。",
                score=0.92,
                relevance_reason=f"[MOCK] '{query}' に関連するコンテンツが含まれています",
            ),
        ]

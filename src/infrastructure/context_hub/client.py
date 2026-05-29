"""
Context-Hub REST API クライアント抽象化インターフェース。
モック実装への切り替えで Context-Hub 本体なしで AI-PM を先行開発できる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# ============================================================
# レスポンス DTO（Context-Hub API レスポンスのデータクラス）
# ============================================================


@dataclass(frozen=True)
class MeetingExtractedTask:
    """Context-Hub GET /meetings/{id} の extractedTasks 要素。"""

    title: str
    suggested_assignee: str | None = None
    suggested_due_date: str | None = None  # ISO 8601 文字列


@dataclass(frozen=True)
class MeetingResponse:
    meeting_id: str
    title: str
    meeting_at: str  # ISO 8601
    participants: list[str] = field(default_factory=list)
    raw_transcript: str | None = None
    summary: str | None = None
    decisions: list[str] = field(default_factory=list)
    extracted_tasks: list[MeetingExtractedTask] = field(default_factory=list)


@dataclass(frozen=True)
class MemberResponse:
    """Context-Hub GET /members の要素。skills/availability は含まれない（v1.0 スコープ外）。"""

    external_id: str
    name: str
    sources: list[str] = field(default_factory=list)
    assigned_issue_count: int = 0
    last_activity_at: str | None = None


@dataclass(frozen=True)
class IssueAssignee:
    external_id: str
    name: str


@dataclass(frozen=True)
class IssueResponse:
    """Context-Hub GET /issues の要素。IssueStatus は正規化済み。"""

    issue_id: str
    source_type: str  # "backlog" | "redmine"
    external_id: str
    title: str
    description: str
    status: str  # "open" | "in_progress" | "resolved" | "closed"
    priority: str  # "urgent" | "high" | "normal" | "low"
    due_date: str | None = None  # ISO 8601 日付文字列
    assignee: IssueAssignee | None = None
    labels: list[str] = field(default_factory=list)
    comment_count: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ProjectContextResponse:
    project_id: str
    name: str
    summary: str
    active_sources: list[str] = field(default_factory=list)
    document_count: int = 0
    issue_count: int = 0


@dataclass(frozen=True)
class SearchResult:
    document_id: str
    source_type: str
    title: str
    snippet: str
    score: float
    relevance_reason: str | None = None


# ============================================================
# クライアントインターフェース
# ============================================================


@runtime_checkable
class ContextHubClient(Protocol):
    """Context-Hub API クライアント抽象化。"""

    async def get_project_context(
        self,
        context_hub_project_id: str,
        context_type: str = "overview",
    ) -> ProjectContextResponse: ...

    async def get_members(
        self,
        context_hub_project_id: str,
    ) -> list[MemberResponse]: ...

    async def get_meeting(
        self,
        context_hub_project_id: str,
        meeting_id: str,
    ) -> MeetingResponse: ...

    async def get_issues(
        self,
        context_hub_project_id: str,
        source: str,
        status: str = "open",
        updated_since: str | None = None,
        limit: int = 50,
    ) -> list[IssueResponse]: ...

    async def search(
        self,
        context_hub_project_id: str,
        query: str,
        top_k: int = 5,
        source_types: list[str] | None = None,
    ) -> list[SearchResult]: ...

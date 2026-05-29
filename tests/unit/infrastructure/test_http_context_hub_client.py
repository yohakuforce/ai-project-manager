"""Cross-service contract test: HttpContextHubClient parses Context-Hub's REAL
camelCase wire format.

Context-Hub serializes responses as camelCase (projectId, externalId,
sourceType, ...). This test feeds the client the exact camelCase shapes
Context-Hub emits and asserts every field maps through without KeyError.

It is the AI-PM half of the contract; the Context-Hub half lives in
Context-Hub/tests/unit/api/test_schema_camelcase_contract.py. Both meet at the
camelCase contract, so a regression on either side surfaces in CI rather than
at runtime against the real service.

The client builds its own httpx.AsyncClient internally, so we monkeypatch it to
ride a MockTransport that returns canned camelCase JSON.
"""

from __future__ import annotations

import httpx
import pytest

from src.infrastructure.context_hub.http_client import HttpContextHubClient

_BASE = "http://context-hub.test/api/v1"


def _envelope(data: dict) -> dict:
    return {"success": True, "data": data, "error": None}


# camelCase payloads — mirror Context-Hub's serialized output (02-api-spec.md).
_CONTEXT = _envelope(
    {
        "projectId": "proj-001",
        "name": "テストプロジェクト",
        "summary": "サマリ",
        "activeSources": ["slack", "backlog"],
        "lastSyncedAt": {"slack": "2026-05-29T00:00:00Z"},
        "documentCount": 142,
        "issueCount": 38,
    }
)
_MEMBERS = _envelope(
    {
        "members": [
            {
                "externalId": "123",
                "name": "田中 太郎",
                "sources": ["backlog"],
                "assignedIssueCount": 5,
                "lastActivityAt": "2026-05-13T18:00:00Z",
            }
        ]
    }
)
_MEETING = _envelope(
    {
        "id": "m-1",
        "title": "週次進捗会議",
        "meetingAt": "2026-05-13T10:00:00Z",
        "participants": ["田中 太郎"],
        "rawTranscript": "（全文）",
        "summary": "（サマリ）",
        "decisions": ["API 仕様を 5/20 までに確定"],
        "extractedTasks": [
            {
                "title": "API 仕様ドラフト作成",
                "suggestedAssignee": "田中 太郎",
                "suggestedDueDate": "2026-05-20",
            }
        ],
    }
)
_ISSUES = _envelope(
    {
        "issues": [
            {
                "id": "i-1",
                "sourceType": "backlog",
                "externalId": "42",
                "title": "ログイン修正",
                "description": "（説明）",
                "status": "in_progress",
                "priority": "high",
                "assignee": {"externalId": "123", "name": "田中 太郎"},
                "dueDate": "2026-05-20",
                "labels": ["フロントエンド"],
                "commentCount": 3,
                "createdAt": "2026-05-10T09:00:00Z",
                "updatedAt": "2026-05-13T18:00:00Z",
            }
        ],
        "total": 38,
        "limit": 50,
        "offset": 0,
    }
)
_QUERY = _envelope(
    {
        "results": [
            {
                "documentId": "d-1",
                "sourceType": "meeting",
                "title": "設計レビュー会議",
                "snippet": "JWT で合意",
                "score": 0.92,
                "relevanceReason": "認証設計の決定",
            }
        ],
        "queryEmbeddingModel": "text-embedding-3-small",
    }
)


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/context"):
        return httpx.Response(200, json=_CONTEXT)
    if path.endswith("/members"):
        return httpx.Response(200, json=_MEMBERS)
    if "/meetings/" in path:
        return httpx.Response(200, json=_MEETING)
    if path.endswith("/issues"):
        return httpx.Response(200, json=_ISSUES)
    if path.endswith("/query"):
        return httpx.Response(200, json=_QUERY)
    return httpx.Response(404, json={"success": False, "data": None, "error": {"code": "X"}})


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> HttpContextHubClient:
    transport = httpx.MockTransport(_handler)
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "src.infrastructure.context_hub.http_client.httpx.AsyncClient", _factory
    )
    return HttpContextHubClient(base_url=_BASE, api_key="test-key")


@pytest.mark.asyncio
class TestHttpContextHubClientParsesCamelCase:
    async def test_get_project_context(self, client: HttpContextHubClient) -> None:
        result = await client.get_project_context("proj-001")
        assert result.project_id == "proj-001"
        assert result.active_sources == ["slack", "backlog"]
        assert result.document_count == 142
        assert result.issue_count == 38

    async def test_get_members(self, client: HttpContextHubClient) -> None:
        members = await client.get_members("proj-001")
        assert members[0].external_id == "123"
        assert members[0].assigned_issue_count == 5
        assert members[0].last_activity_at == "2026-05-13T18:00:00Z"

    async def test_get_meeting(self, client: HttpContextHubClient) -> None:
        meeting = await client.get_meeting("proj-001", "m-1")
        assert meeting.meeting_id == "m-1"
        assert meeting.decisions == ["API 仕様を 5/20 までに確定"]
        task = meeting.extracted_tasks[0]
        assert task.suggested_assignee == "田中 太郎"
        assert task.suggested_due_date == "2026-05-20"

    async def test_get_issues(self, client: HttpContextHubClient) -> None:
        issues = await client.get_issues("proj-001", source="backlog")
        issue = issues[0]
        assert issue.source_type == "backlog"
        assert issue.external_id == "42"
        assert issue.due_date == "2026-05-20"
        assert issue.comment_count == 3
        assert issue.assignee is not None
        assert issue.assignee.external_id == "123"

    async def test_search(self, client: HttpContextHubClient) -> None:
        results = await client.search("proj-001", query="認証")
        assert results[0].document_id == "d-1"
        assert results[0].source_type == "meeting"
        assert results[0].relevance_reason == "認証設計の決定"


@pytest.mark.asyncio
async def test_snake_case_response_would_break(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard: a snake_case regression on Context-Hub's side must blow up loudly.

    Documents WHY the camelCase contract matters — this is the exact failure
    (KeyError on 'projectId') that shipped silently before the contract fix.
    """
    snake = _envelope(
        {
            "project_id": "proj-001",
            "name": "p",
            "summary": "s",
            "active_sources": [],
            "document_count": 0,
            "issue_count": 0,
        }
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=snake))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "src.infrastructure.context_hub.http_client.httpx.AsyncClient",
        lambda *a, **k: real_async_client(*a, **{**k, "transport": transport}),
    )
    client = HttpContextHubClient(base_url=_BASE, api_key="k")
    with pytest.raises(KeyError):
        await client.get_project_context("proj-001")

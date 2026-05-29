"""
Context-Hub モッククライアントのユニットテスト。
モックが期待通りのスキーマを返すことを検証する。
"""

from __future__ import annotations

import pytest

from src.infrastructure.context_hub import MockContextHubClient


@pytest.fixture
def client() -> MockContextHubClient:
    return MockContextHubClient()


@pytest.mark.asyncio
class TestMockContextHubClient:
    async def test_get_project_context_returns_response(self, client: MockContextHubClient) -> None:
        result = await client.get_project_context("project-001")
        assert result.project_id == "project-001"
        assert result.name is not None
        assert result.summary is not None

    async def test_get_members_returns_list(self, client: MockContextHubClient) -> None:
        result = await client.get_members("project-001")
        assert len(result) > 0
        assert result[0].external_id is not None
        assert result[0].name is not None
        # T-011 確認: skills/availability は含まれない（Context-Hub v1.0 スコープ外）
        for member in result:
            assert not hasattr(member, "skills")
            assert not hasattr(member, "availability")

    async def test_get_meeting_returns_extracted_tasks(self, client: MockContextHubClient) -> None:
        result = await client.get_meeting("project-001", "meeting-001")
        assert result.meeting_id == "meeting-001"
        assert len(result.extracted_tasks) > 0
        # T-011 確認: extractedTasks のスキーマ検証
        for task in result.extracted_tasks:
            assert task.title is not None
            # suggestedAssignee / suggestedDueDate はオプション

    async def test_get_issues_returns_list(self, client: MockContextHubClient) -> None:
        result = await client.get_issues("project-001", source="backlog")
        assert len(result) > 0
        issue = result[0]
        # T-011 確認: IssueStatus が正規化済みであること
        assert issue.status in ("open", "in_progress", "resolved", "closed")
        assert issue.priority in ("urgent", "high", "normal", "low")

    async def test_search_returns_results(self, client: MockContextHubClient) -> None:
        results = await client.search("project-001", "認証設計")
        assert len(results) > 0
        assert 0.0 <= results[0].score <= 1.0

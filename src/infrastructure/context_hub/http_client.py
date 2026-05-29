"""
Context-Hub HTTP クライアント実装。
CONTEXT_HUB_USE_MOCK=false の場合に使用する。
"""

from __future__ import annotations

import httpx

from .client import (
    IssueAssignee,
    IssueResponse,
    MeetingExtractedTask,
    MeetingResponse,
    MemberResponse,
    ProjectContextResponse,
    SearchResult,
)


class HttpContextHubClient:
    """Context-Hub REST API への HTTP 通信を行う実装クラス。"""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
        }

    async def get_project_context(
        self,
        context_hub_project_id: str,
        context_type: str = "overview",
    ) -> ProjectContextResponse:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self._base_url}/projects/{context_hub_project_id}/context",
                params={"type": context_type},
                headers=self._headers,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()["data"]
            return ProjectContextResponse(
                project_id=data["projectId"],
                name=data["name"],
                summary=data.get("summary", ""),
                active_sources=data.get("activeSources", []),
                document_count=data.get("documentCount", 0),
                issue_count=data.get("issueCount", 0),
            )

    async def get_members(
        self,
        context_hub_project_id: str,
    ) -> list[MemberResponse]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self._base_url}/projects/{context_hub_project_id}/members",
                headers=self._headers,
                timeout=30.0,
            )
            response.raise_for_status()
            members_data = response.json()["data"]["members"]
            return [
                MemberResponse(
                    external_id=m["externalId"],
                    name=m["name"],
                    sources=m.get("sources", []),
                    assigned_issue_count=m.get("assignedIssueCount", 0),
                    last_activity_at=m.get("lastActivityAt"),
                )
                for m in members_data
            ]

    async def get_meeting(
        self,
        context_hub_project_id: str,
        meeting_id: str,
    ) -> MeetingResponse:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self._base_url}/projects/{context_hub_project_id}/meetings/{meeting_id}",
                headers=self._headers,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()["data"]
            extracted_tasks = [
                MeetingExtractedTask(
                    title=t["title"],
                    suggested_assignee=t.get("suggestedAssignee"),
                    suggested_due_date=t.get("suggestedDueDate"),
                )
                for t in data.get("extractedTasks", [])
            ]
            return MeetingResponse(
                meeting_id=data["id"],
                title=data["title"],
                meeting_at=data["meetingAt"],
                participants=data.get("participants", []),
                raw_transcript=data.get("rawTranscript"),
                summary=data.get("summary"),
                decisions=data.get("decisions", []),
                extracted_tasks=extracted_tasks,
            )

    async def get_issues(
        self,
        context_hub_project_id: str,
        source: str,
        status: str = "open",
        updated_since: str | None = None,
        limit: int = 50,
    ) -> list[IssueResponse]:
        params: dict = {"source": source, "status": status, "limit": limit}
        if updated_since:
            params["updatedSince"] = updated_since

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self._base_url}/projects/{context_hub_project_id}/issues",
                params=params,
                headers=self._headers,
                timeout=30.0,
            )
            response.raise_for_status()
            issues_data = response.json()["data"]["issues"]
            return [
                IssueResponse(
                    issue_id=issue["id"],
                    source_type=issue["sourceType"],
                    external_id=issue["externalId"],
                    title=issue["title"],
                    description=issue.get("description", ""),
                    status=issue["status"],
                    priority=issue["priority"],
                    due_date=issue.get("dueDate"),
                    assignee=(
                        IssueAssignee(
                            external_id=issue["assignee"]["externalId"],
                            name=issue["assignee"]["name"],
                        )
                        if issue.get("assignee")
                        else None
                    ),
                    labels=issue.get("labels", []),
                    comment_count=issue.get("commentCount", 0),
                    created_at=issue.get("createdAt", ""),
                    updated_at=issue.get("updatedAt", ""),
                )
                for issue in issues_data
            ]

    async def search(
        self,
        context_hub_project_id: str,
        query: str,
        top_k: int = 5,
        source_types: list[str] | None = None,
    ) -> list[SearchResult]:
        payload: dict = {"projectId": context_hub_project_id, "query": query, "topK": top_k}
        if source_types:
            payload["sourceTypes"] = source_types

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/query",
                json=payload,
                headers=self._headers,
                timeout=30.0,
            )
            response.raise_for_status()
            results_data = response.json()["data"]["results"]
            return [
                SearchResult(
                    document_id=r["documentId"],
                    source_type=r["sourceType"],
                    title=r["title"],
                    snippet=r["snippet"],
                    score=r["score"],
                    relevance_reason=r.get("relevanceReason"),
                )
                for r in results_data
            ]

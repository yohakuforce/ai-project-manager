"""AuditingLLMAdapter のユニットテスト。"""

from __future__ import annotations

import hashlib

import pytest

from src.domain.audit.aggregate import AuditAction
from src.infrastructure.audit.in_memory import InMemoryAuditLogRepository
from src.infrastructure.llm.auditing_adapter import AuditingLLMAdapter, _estimate_cost
from src.infrastructure.llm.mock_adapter import MockLLMAdapter


@pytest.mark.asyncio
class TestAuditingLLMAdapter:
    async def test_records_llm_call_audit_with_hashed_prompt(self) -> None:
        audit_repo = InMemoryAuditLogRepository()
        inner = MockLLMAdapter(fixed_response="OK")
        adapter = AuditingLLMAdapter(inner=inner, audit_repository=audit_repo)

        prompt = "summarize this please"
        response = await adapter.generate(prompt)

        assert response.content == "OK"

        logs = [log for log in audit_repo.all_logs if log.action == AuditAction.LLM_CALL]
        assert len(logs) == 1
        recorded = logs[0]
        assert recorded.actor == "ai-agent"
        assert recorded.llm_model == response.model
        assert recorded.input_hash == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        assert recorded.token_usage is not None
        assert recorded.token_usage.prompt_tokens == response.prompt_tokens
        assert recorded.token_usage.completion_tokens == response.completion_tokens
        assert recorded.token_usage.estimated_cost_usd >= 0.0

    async def test_does_not_store_raw_prompt(self) -> None:
        """生プロンプトは絶対に保存しないことを確認。"""
        audit_repo = InMemoryAuditLogRepository()
        adapter = AuditingLLMAdapter(
            inner=MockLLMAdapter(fixed_response="OK"),
            audit_repository=audit_repo,
        )

        prompt = "シークレット情報を含むプロンプト"
        await adapter.generate(prompt)

        log = audit_repo.all_logs[0]
        assert prompt not in (log.input_hash or "")
        # 64 文字 hex（SHA-256）であることを確認
        assert log.input_hash is not None
        assert len(log.input_hash) == 64

    async def test_records_each_generate_call(self) -> None:
        audit_repo = InMemoryAuditLogRepository()
        adapter = AuditingLLMAdapter(
            inner=MockLLMAdapter(fixed_response="OK"),
            audit_repository=audit_repo,
        )

        await adapter.generate("first")
        await adapter.generate("second")
        await adapter.generate("third")

        llm_logs = [log for log in audit_repo.all_logs if log.action == AuditAction.LLM_CALL]
        assert len(llm_logs) == 3
        hashes = {log.input_hash for log in llm_logs}
        assert len(hashes) == 3  # 各プロンプト固有のハッシュ

    async def test_project_id_resolver_is_called(self) -> None:
        audit_repo = InMemoryAuditLogRepository()
        adapter = AuditingLLMAdapter(
            inner=MockLLMAdapter(fixed_response="OK"),
            audit_repository=audit_repo,
            project_id_resolver=lambda: "p-001",
        )

        await adapter.generate("x")

        assert audit_repo.all_logs[0].project_id == "p-001"

    async def test_audit_failure_does_not_break_generation(self) -> None:
        """監査記録が失敗しても LLM レスポンスは返る。"""

        class FailingRepo(InMemoryAuditLogRepository):
            async def append(self, log):  # type: ignore[override]
                raise RuntimeError("disk full")

        adapter = AuditingLLMAdapter(
            inner=MockLLMAdapter(fixed_response="OK"),
            audit_repository=FailingRepo(),
        )

        response = await adapter.generate("x")
        assert response.content == "OK"


class TestAuditingLLMAdapterMisc:
    def test_model_name_delegates_to_inner(self) -> None:
        inner = MockLLMAdapter(fixed_response="OK")
        adapter = AuditingLLMAdapter(
            inner=inner,
            audit_repository=InMemoryAuditLogRepository(),
        )
        assert adapter.model_name == inner.model_name


class TestEstimateCost:
    def test_known_model_cost_is_positive(self) -> None:
        cost = _estimate_cost("claude-sonnet-4-6", prompt_tokens=1000, completion_tokens=500)
        assert cost > 0.0

    def test_unknown_model_returns_zero_cost(self) -> None:
        cost = _estimate_cost("subscription-only-cli", prompt_tokens=10000, completion_tokens=5000)
        assert cost == 0.0

"""Context-Hub クライアントのファクトリ。"""

from __future__ import annotations

from src.config import get_settings

from .client import ContextHubClient
from .http_client import HttpContextHubClient
from .mock_client import MockContextHubClient


def create_context_hub_client() -> ContextHubClient:
    """
    CONTEXT_HUB_USE_MOCK に基づいてクライアントを生成する。
    Context-Hub が未完成の段階では mock=true で先行開発できる。
    """
    settings = get_settings()
    if settings.context_hub_use_mock:
        return MockContextHubClient()
    return HttpContextHubClient(
        base_url=settings.context_hub_base_url,
        api_key=settings.context_hub_api_key,
    )

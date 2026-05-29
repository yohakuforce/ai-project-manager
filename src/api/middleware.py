"""
認証 Middleware — X-Api-Key ヘッダーによる API キー認証。

方針:
  - `X-Api-Key` ヘッダーで認証
  - 設定値 (APP_SECRET_KEY) と照合
  - /health と /docs 系は認証スキップ
"""

from __future__ import annotations

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from src.config import get_settings

_API_KEY_HEADER = APIKeyHeader(name="X-Api-Key", auto_error=False)
_SKIP_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


async def verify_api_key(api_key: str = Security(_API_KEY_HEADER)) -> str:
    """
    FastAPI Dependency で使用する API キー検証関数。

    Usage:
        @router.post("/", dependencies=[Depends(verify_api_key)])
        async def endpoint(): ...
    """
    settings = get_settings()
    if not api_key or api_key != settings.app_secret_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Api-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key

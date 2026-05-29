"""AuditLogRepository ファクトリ。"""

from __future__ import annotations

import logging
from pathlib import Path

from src.config.settings import Settings
from src.domain.audit.repository import AuditLogRepository
from src.infrastructure.audit.in_memory import InMemoryAuditLogRepository
from src.infrastructure.audit.jsonl import JsonlAuditLogRepository

logger = logging.getLogger(__name__)


def build_audit_log_repository(settings: Settings) -> AuditLogRepository:
    """settings から AuditLogRepository を構築する。

    判定:
      - ``audit_log_dir`` 未設定 → InMemoryAuditLogRepository（テスト・dev 用）
      - 設定済み → JsonlAuditLogRepository（社内 PC ローカルファイル）
    """
    log_dir = getattr(settings, "audit_log_dir", "") or ""
    if not log_dir:
        logger.warning(
            "AUDIT_LOG_DIR が未設定のため InMemoryAuditLogRepository を使用します。"
            "production では必ず AUDIT_LOG_DIR を設定してください。"
        )
        return InMemoryAuditLogRepository()
    return JsonlAuditLogRepository(Path(log_dir))

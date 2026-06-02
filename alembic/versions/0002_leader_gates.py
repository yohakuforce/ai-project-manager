"""leader gates

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-02 21:00:00.000000

リーダー確認ゲート（LeaderGate）の永続化テーブルを追加する。
確認が翌日になっても保持できるよう、PENDING ゲートを DB に保存する。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "leader_gates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("gate_type", sa.String(50), nullable=False),
        sa.Column("gate_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("context_json", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("decision", sa.String(50)),
        sa.Column("resolved_by", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_leader_gates_project_id", "leader_gates", ["project_id"])
    op.create_index("ix_leader_gates_gate_date", "leader_gates", ["gate_date"])
    op.create_index("ix_leader_gates_status", "leader_gates", ["status"])


def downgrade() -> None:
    op.drop_table("leader_gates")

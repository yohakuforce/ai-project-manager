"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-15 00:00:00.000000

全 ORM テーブルの初回マイグレーション。
テーブル:
  - projects
  - tasks
  - assignments
  - members
  - daily_reports
  - alerts
  - audit_logs
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- projects ---
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("customer", sa.String(255), nullable=False),
        sa.Column("goal", sa.Text, nullable=False),
        sa.Column("context_hub_project_id", sa.String(36), nullable=False),
        sa.Column("context_hub_api_endpoint", sa.String(512), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("phases_json", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("issue_status_mappings_json", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- tasks ---
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("priority", sa.String(50), nullable=False, server_default="normal"),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_ref", sa.String(255)),
        sa.Column("due_date", sa.Date),
        sa.Column("estimated_hours", sa.Float),
        sa.Column("dependencies_json", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("ai_confidence", sa.Float),
    )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])

    # --- assignments ---
    op.create_table(
        "assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("member_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
        sa.Column("ai_rationale", sa.Text, nullable=False),
        sa.Column("confirmed_by", sa.String(255)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_assignments_project_id", "assignments", ["project_id"])
    op.create_index("ix_assignments_task_id", "assignments", ["task_id"])
    op.create_index("ix_assignments_member_id", "assignments", ["member_id"])

    # --- members ---
    op.create_table(
        "members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("skills_json", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("availability_json", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("performance_history_json", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_members_external_id", "members", ["external_id"], unique=True)

    # --- daily_reports ---
    op.create_table(
        "daily_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("member_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("report_date", sa.Date, nullable=False),
        sa.Column("template_json", postgresql.JSONB, nullable=False),
        sa.Column("responses_json", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("ai_summary", sa.Text),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("analyzed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_daily_reports_member_id", "daily_reports", ["member_id"])
    op.create_index("ix_daily_reports_project_id", "daily_reports", ["project_id"])
    op.create_index("ix_daily_reports_report_date", "daily_reports", ["report_date"])

    # --- alerts ---
    op.create_table(
        "alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(50), nullable=False),
        sa.Column("ai_generated_message", sa.Text, nullable=False),
        sa.Column("evidence_json", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("target_task_id", sa.String(36)),
        sa.Column("target_member_id", sa.String(36)),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("acknowledged_by", sa.String(255)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_alerts_project_id", "alerts", ["project_id"])

    # --- audit_logs ---
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("project_id", sa.String(36)),
        sa.Column("data_ref", sa.String(255)),
        sa.Column("llm_model", sa.String(100)),
        sa.Column("token_usage_json", postgresql.JSONB),
        sa.Column("input_hash", sa.String(64)),
    )
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])
    op.create_index("ix_audit_logs_project_id", "audit_logs", ["project_id"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("alerts")
    op.drop_table("daily_reports")
    op.drop_table("members")
    op.drop_table("assignments")
    op.drop_table("tasks")
    op.drop_table("projects")

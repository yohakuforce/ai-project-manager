"""
SQLAlchemy ORM モデル定義。
Alembic マイグレーションのソース。

設計方針:
  - ドメインモデル（aggregate）と ORM モデルは分離する
  - ORM モデルは永続化に特化した薄いマッピング層
  - UUID は String(36) で保管（PostgreSQL では native UUID 型でも可）
  - JSON カラム（skills / availability / evidence 等）は JSONB で保管
  - datetime はすべて UTC タイムゾーン付きで保管
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

# PostgreSQL では JSONB、それ以外（SQLite テスト等）では汎用 JSON にディスパッチする。
# Alembic マイグレーションは PostgreSQL を前提とするため JSONB のまま生成される。
JSONType = JSON().with_variant(JSONB(), "postgresql")

# ============================================================
# Project Management Context
# ============================================================


class ProjectModel(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    customer: Mapped[str] = mapped_column(String(255), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    context_hub_project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    context_hub_api_endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    # Phases / Milestones は JSONB で非正規化（集約内エンティティのため）
    phases_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=list)
    issue_status_mappings_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    tasks: Mapped[list[TaskModel]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    assignments: Mapped[list[AssignmentModel]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    project_members: Mapped[list[ProjectMemberModel]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class TaskModel(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    priority: Mapped[str] = mapped_column(String(50), nullable=False, default="normal")
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255))
    due_date: Mapped[date | None] = mapped_column(Date)
    estimated_hours: Mapped[float | None] = mapped_column(Float)
    dependencies_json: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    ai_confidence: Mapped[float | None] = mapped_column(Float)

    project: Mapped[ProjectModel] = relationship(back_populates="tasks")


class AssignmentModel(Base):
    __tablename__ = "assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    ai_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(255))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[ProjectModel] = relationship(back_populates="assignments")


# ============================================================
# Member Context
# ============================================================


class MemberModel(Base):
    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    # skills / availability / performance_history は JSONB で非正規化
    skills_json: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    availability_json: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    performance_history_json: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    project_members: Mapped[list[ProjectMemberModel]] = relationship(
        back_populates="member", cascade="all, delete-orphan"
    )


class ProjectMemberModel(Base):
    """Project ↔ Member 多対多ブリッジテーブル。"""

    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "member_id", name="uq_project_members"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    member_id: Mapped[str] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    project: Mapped[ProjectModel] = relationship(back_populates="project_members")
    member: Mapped[MemberModel] = relationship(back_populates="project_members")


# ============================================================
# Reporting Context
# ============================================================


class DailyReportModel(Base):
    __tablename__ = "daily_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    template_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    responses_json: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ============================================================
# Alert Context
# ============================================================


class AlertModel(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False)
    ai_generated_message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    target_task_id: Mapped[str | None] = mapped_column(String(36))
    target_member_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    acknowledged_by: Mapped[str | None] = mapped_column(String(255))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ============================================================
# Audit Context
# ============================================================


class AuditLogModel(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    data_ref: Mapped[str | None] = mapped_column(String(255))
    llm_model: Mapped[str | None] = mapped_column(String(100))
    token_usage_json: Mapped[dict | None] = mapped_column(JSONType)
    input_hash: Mapped[str | None] = mapped_column(String(64))  # SHA-256


# ============================================================
# Leader Gate Context（リーダー確認ゲート）
# ============================================================


class LeaderGateModel(Base):
    __tablename__ = "leader_gates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    gate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    gate_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True)
    context_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    decision: Mapped[str | None] = mapped_column(String(50))
    resolved_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

"""ドメインモデル ⇄ ORM モデルの双方向マッパー。

設計方針:
  - 純関数。状態を持たない。
  - ドメイン層は SQLAlchemy を知らない（このモジュールが境界）。
  - JSONB カラムは dict/list で扱う（datetime は ISO 文字列化）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from src.domain.alert.aggregate import (
    Alert,
    AlertCategory,
    AlertId,
    AlertSeverity,
    AlertStatus,
    Evidence,
    EvidenceType,
)
from src.domain.member.aggregate import Member
from src.domain.member.value_objects import (
    Availability,
    MemberId,
    MemberRole,
    PerformanceHistory,
    Skill,
    SkillCategory,
    SkillLevel,
)
from src.domain.project.aggregate import Project
from src.domain.project.entities import Assignment, Task
from src.domain.project.value_objects import (
    AssignmentId,
    AssignmentStatus,
    ContextHubProjectRef,
    IssueStatusMapping,
    Milestone,
    MilestoneId,
    Phase,
    PhaseId,
    ProjectId,
    ProjectStatus,
    TaskId,
    TaskPriority,
    TaskSource,
    TaskStatus,
)
from src.domain.reporting.aggregate import DailyReport
from src.domain.reporting.value_objects import (
    DailyReportId,
    QuestionId,
    QuestionType,
    ReportQuestion,
    ReportResponse,
    ReportStatus,
    ReportTemplate,
)
from src.infrastructure.db.models import (
    AlertModel,
    AssignmentModel,
    DailyReportModel,
    MemberModel,
    ProjectModel,
    TaskModel,
)

# ============================================================
# Project
# ============================================================


def _phase_to_dict(phase: Phase) -> dict:
    return {
        "phase_id": str(phase.phase_id),
        "name": phase.name,
        "start_date": phase.start_date.isoformat(),
        "planned_end_date": phase.planned_end_date.isoformat(),
        "completion_criteria": phase.completion_criteria,
        "milestones": [
            {
                "milestone_id": str(m.milestone_id),
                "name": m.name,
                "due_date": m.due_date.isoformat(),
            }
            for m in phase.milestones
        ],
    }


def _phase_from_dict(data: dict) -> Phase:
    return Phase(
        phase_id=PhaseId.from_str(data["phase_id"]),
        name=data["name"],
        start_date=date.fromisoformat(data["start_date"]),
        planned_end_date=date.fromisoformat(data["planned_end_date"]),
        completion_criteria=data["completion_criteria"],
        milestones=tuple(
            Milestone(
                milestone_id=MilestoneId(value=uuid.UUID(m["milestone_id"])),
                name=m["name"],
                due_date=date.fromisoformat(m["due_date"]),
            )
            for m in data.get("milestones", [])
        ),
    )


def _mapping_to_dict(mapping: IssueStatusMapping) -> dict:
    return {
        "source_type": mapping.source_type,
        "external_status_name": mapping.external_status_name,
        "internal_status": mapping.internal_status.value,
    }


def _mapping_from_dict(data: dict) -> IssueStatusMapping:
    return IssueStatusMapping(
        source_type=data["source_type"],
        external_status_name=data["external_status_name"],
        internal_status=TaskStatus(data["internal_status"]),
    )


def task_to_model(task: Task, project_id: str) -> TaskModel:
    return TaskModel(
        id=str(task.task_id),
        project_id=project_id,
        title=task.title,
        description=task.description,
        status=task.status.value,
        priority=task.priority.value,
        source=task.source.value,
        source_ref=task.source_ref,
        due_date=task.due_date,
        estimated_hours=task.estimated_hours,
        dependencies_json=[str(d) for d in task.dependencies],
        ai_confidence=task.ai_confidence,
    )


def task_from_model(model: TaskModel) -> Task:
    return Task(
        task_id=TaskId.from_str(model.id),
        title=model.title,
        description=model.description,
        status=TaskStatus(model.status),
        priority=TaskPriority(model.priority),
        source=TaskSource(model.source),
        source_ref=model.source_ref,
        due_date=model.due_date,
        estimated_hours=model.estimated_hours,
        dependencies=[TaskId.from_str(d) for d in (model.dependencies_json or [])],
        ai_confidence=model.ai_confidence,
    )


def assignment_to_model(assignment: Assignment, project_id: str) -> AssignmentModel:
    return AssignmentModel(
        id=str(assignment.assignment_id),
        project_id=project_id,
        task_id=str(assignment.task_id),
        member_id=assignment.member_id,
        status=assignment.status.value,
        ai_rationale=assignment.ai_rationale,
        confirmed_by=assignment.confirmed_by,
        confirmed_at=assignment.confirmed_at,
    )


def assignment_from_model(model: AssignmentModel) -> Assignment:
    return Assignment(
        assignment_id=AssignmentId(value=uuid.UUID(model.id)),
        task_id=TaskId.from_str(model.task_id),
        member_id=model.member_id,
        status=AssignmentStatus(model.status),
        ai_rationale=model.ai_rationale,
        confirmed_by=model.confirmed_by,
        confirmed_at=model.confirmed_at,
    )


def project_to_model(project: Project) -> ProjectModel:
    """Project を ORM モデルに変換する（子要素含む新規 model を返す）。"""
    return ProjectModel(
        id=str(project.project_id),
        name=project.name,
        customer=project.customer,
        goal=project.goal,
        context_hub_project_id=project.context_hub_ref.context_hub_project_id,
        context_hub_api_endpoint=project.context_hub_ref.api_endpoint,
        status=project.status.value,
        phases_json=[_phase_to_dict(p) for p in project.phases],
        issue_status_mappings_json=[_mapping_to_dict(m) for m in project.issue_status_mappings],
        created_at=project.created_at,
        updated_at=project.updated_at,
        tasks=[task_to_model(t, str(project.project_id)) for t in project.tasks],
        assignments=[assignment_to_model(a, str(project.project_id)) for a in project.assignments],
    )


def project_from_model(model: ProjectModel) -> Project:
    return Project(
        project_id=ProjectId.from_str(model.id),
        name=model.name,
        customer=model.customer,
        goal=model.goal,
        context_hub_ref=ContextHubProjectRef(
            context_hub_project_id=model.context_hub_project_id,
            api_endpoint=model.context_hub_api_endpoint,
        ),
        phases=[_phase_from_dict(p) for p in (model.phases_json or [])],
        tasks=[task_from_model(t) for t in (model.tasks or [])],
        assignments=[assignment_from_model(a) for a in (model.assignments or [])],
        issue_status_mappings=[
            _mapping_from_dict(m) for m in (model.issue_status_mappings_json or [])
        ],
        status=ProjectStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


# ============================================================
# Member
# ============================================================


def _skill_to_dict(skill: Skill) -> dict:
    return {
        "category": skill.category.value,
        "name": skill.name,
        "level": skill.level.value,
        "years_of_experience": skill.years_of_experience,
    }


def _skill_from_dict(data: dict) -> Skill:
    return Skill(
        category=SkillCategory(data["category"]),
        name=data["name"],
        level=SkillLevel(data["level"]),
        years_of_experience=float(data["years_of_experience"]),
    )


def _availability_to_dict(a: Availability) -> dict:
    return {
        "date": a.date.isoformat(),
        "available_hours": a.available_hours,
        "note": a.note,
    }


def _availability_from_dict(data: dict) -> Availability:
    return Availability(
        date=date.fromisoformat(data["date"]),
        available_hours=float(data["available_hours"]),
        note=data.get("note"),
    )


def _performance_to_dict(p: PerformanceHistory) -> dict:
    return {
        "task_id": p.task_id,
        "completed_at": p.completed_at.isoformat(),
        "delay_days": p.delay_days,
        "quality_note": p.quality_note,
    }


def _performance_from_dict(data: dict) -> PerformanceHistory:
    return PerformanceHistory(
        task_id=data["task_id"],
        completed_at=date.fromisoformat(data["completed_at"]),
        delay_days=int(data["delay_days"]),
        quality_note=data.get("quality_note"),
    )


def member_to_model(member: Member) -> MemberModel:
    return MemberModel(
        id=str(member.member_id),
        external_id=member.external_id,
        name=member.name,
        role=member.role.value,
        skills_json=[_skill_to_dict(s) for s in member.skills],
        availability_json=[_availability_to_dict(a) for a in member.availability],
        performance_history_json=[_performance_to_dict(p) for p in member.performance_history],
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


def member_from_model(model: MemberModel) -> Member:
    return Member(
        member_id=MemberId.from_str(model.id),
        external_id=model.external_id,
        name=model.name,
        role=MemberRole(model.role),
        skills=[_skill_from_dict(s) for s in (model.skills_json or [])],
        availability=[_availability_from_dict(a) for a in (model.availability_json or [])],
        performance_history=[
            _performance_from_dict(p) for p in (model.performance_history_json or [])
        ],
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


# ============================================================
# Alert
# ============================================================


def _evidence_to_dict(e: Evidence) -> dict:
    return {
        "evidence_type": e.evidence_type.value,
        "data_ref": e.data_ref,
        "human_readable_summary": e.human_readable_summary,
    }


def _evidence_from_dict(data: dict) -> Evidence:
    return Evidence(
        evidence_type=EvidenceType(data["evidence_type"]),
        data_ref=data["data_ref"],
        human_readable_summary=data["human_readable_summary"],
    )


def alert_to_model(alert: Alert) -> AlertModel:
    return AlertModel(
        id=str(alert.alert_id),
        project_id=alert.project_id,
        category=alert.category.value,
        severity=alert.severity.value,
        ai_generated_message=alert.ai_generated_message,
        evidence_json=[_evidence_to_dict(e) for e in alert.evidence],
        target_task_id=alert.target_task_id,
        target_member_id=alert.target_member_id,
        status=alert.status.value,
        acknowledged_by=alert.acknowledged_by,
        acknowledged_at=alert.acknowledged_at,
        detected_at=alert.detected_at,
        resolved_at=alert.resolved_at,
    )


def alert_from_model(model: AlertModel) -> Alert:
    return Alert(
        alert_id=AlertId(value=uuid.UUID(model.id)),
        project_id=model.project_id,
        category=AlertCategory(model.category),
        severity=AlertSeverity(model.severity),
        ai_generated_message=model.ai_generated_message,
        evidence=[_evidence_from_dict(e) for e in (model.evidence_json or [])],
        target_task_id=model.target_task_id,
        target_member_id=model.target_member_id,
        status=AlertStatus(model.status),
        acknowledged_by=model.acknowledged_by,
        acknowledged_at=model.acknowledged_at,
        detected_at=model.detected_at,
        resolved_at=model.resolved_at,
    )


# ============================================================
# DailyReport
# ============================================================


def _question_to_dict(q: ReportQuestion) -> dict:
    return {
        "question_id": str(q.question_id),
        "question_type": q.question_type.value,
        "body": q.body,
        "task_id": q.task_id,
    }


def _question_from_dict(data: dict) -> ReportQuestion:
    return ReportQuestion(
        question_id=QuestionId(value=uuid.UUID(data["question_id"])),
        question_type=QuestionType(data["question_type"]),
        body=data["body"],
        task_id=data.get("task_id"),
    )


def _template_to_dict(t: ReportTemplate) -> dict:
    return {
        "generated_at": t.generated_at.isoformat(),
        "questions": [_question_to_dict(q) for q in t.questions],
    }


def _template_from_dict(data: dict) -> ReportTemplate:
    return ReportTemplate(
        generated_at=datetime.fromisoformat(data["generated_at"]),
        questions=tuple(_question_from_dict(q) for q in data.get("questions", [])),
    )


def _response_to_dict(r: ReportResponse) -> dict:
    return {
        "question_id": str(r.question_id),
        "response_text": r.response_text,
        "responded_at": r.responded_at.isoformat(),
    }


def _response_from_dict(data: dict) -> ReportResponse:
    return ReportResponse(
        question_id=QuestionId(value=uuid.UUID(data["question_id"])),
        response_text=data["response_text"],
        responded_at=datetime.fromisoformat(data["responded_at"]),
    )


def daily_report_to_model(report: DailyReport) -> DailyReportModel:
    return DailyReportModel(
        id=str(report.report_id),
        member_id=report.member_id,
        project_id=report.project_id,
        report_date=report.report_date,
        template_json=_template_to_dict(report.template),
        responses_json=[_response_to_dict(r) for r in report.responses],
        ai_summary=report.ai_summary,
        status=report.status.value,
        delivered_at=report.delivered_at,
        submitted_at=report.submitted_at,
        analyzed_at=report.analyzed_at,
    )


def daily_report_from_model(model: DailyReportModel) -> DailyReport:
    return DailyReport(
        report_id=DailyReportId(value=uuid.UUID(model.id)),
        member_id=model.member_id,
        project_id=model.project_id,
        report_date=model.report_date,
        template=_template_from_dict(model.template_json),
        responses=[_response_from_dict(r) for r in (model.responses_json or [])],
        ai_summary=model.ai_summary,
        status=ReportStatus(model.status),
        delivered_at=model.delivered_at,
        submitted_at=model.submitted_at,
        analyzed_at=model.analyzed_at,
    )

"""End-to-end demo: AI-PM 5-capability loop against a LIVE Context-Hub.

Exercises Plan -> Assign -> Track -> Alert -> Overview using in-memory
repositories and a deterministic Mock LLM, while pulling REAL issue data from
a running Context-Hub REST server (the HTTP client / camelCase contract path).

Prerequisites:
  1. Context-Hub seeded and serving REST on :8000 (see Context-Hub/scripts/seed_sample.py)
       cd ~/Desktop/01_active/Context-Hub && source .venv/bin/activate
       python scripts/seed_sample.py
       DEV_API_KEY=dev-seed-key APP_ENV=development \
         uvicorn context_hub.main:create_app --factory --host 127.0.0.1 --port 8000

Run:
    cd ~/Desktop/01_active/AI-Project-Manager && source .venv/bin/activate
    CONTEXT_HUB_BASE_URL=http://127.0.0.1:8000/api/v1 \
    CONTEXT_HUB_API_KEY=dev-seed-key \
        python scripts/demo_five_capabilities.py

The Context-Hub project id is "proj-001" (from seed_sample.py).
"""

from __future__ import annotations

import asyncio
import os
from datetime import date

from src.application.alert.service import AlertService
from src.application.assign.service import AssignService
from src.application.overview.service import OverviewService
from src.application.plan.service import PlanService
from src.application.track.service import ResponseInput, TrackService
from src.domain.member.aggregate import Member
from src.domain.member.value_objects import MemberId, MemberRole
from src.domain.project.aggregate import Project
from src.domain.project.value_objects import ContextHubProjectRef, ProjectId
from src.infrastructure.audit.in_memory import InMemoryAuditLogRepository
from src.infrastructure.context_hub.http_client import HttpContextHubClient
from src.infrastructure.llm.mock_adapter import MockLLMAdapter
from src.infrastructure.notifiers.in_memory import InMemoryNotifier
from src.infrastructure.repositories.in_memory import (
    InMemoryAlertRepository,
    InMemoryDailyReportRepository,
    InMemoryMemberRepository,
    InMemoryProjectRepository,
)

CONTEXT_HUB_PROJECT_ID = "proj-001"
MEETING_ID = "meeting-demo-001"  # stable id from Context-Hub/scripts/seed_sample.py
BASE_URL = os.environ.get("CONTEXT_HUB_BASE_URL", "http://127.0.0.1:8000/api/v1")
API_KEY = os.environ.get("CONTEXT_HUB_API_KEY", "dev-seed-key")
SCAN_DATE = date(2026, 6, 4)  # past the seeded due dates (6/2, 6/3) -> delay alerts


def _hr(title: str) -> None:
    print(f"\n{'=' * 8} {title} {'=' * 8}")


async def main() -> None:
    project_repo = InMemoryProjectRepository()
    member_repo = InMemoryMemberRepository()
    report_repo = InMemoryDailyReportRepository()
    alert_repo = InMemoryAlertRepository()
    audit_repo = InMemoryAuditLogRepository()
    llm = MockLLMAdapter(fixed_response="（決定的Mock応答）特記事項なし。")
    notifier = InMemoryNotifier()
    hub = HttpContextHubClient(base_url=BASE_URL, api_key=API_KEY)

    project = Project(
        project_id=ProjectId.generate(),
        name="デモPJ — 基幹システム刷新（マスク済）",
        customer="デモ顧客（マスク済）",
        goal="認証基盤のリプレースを完了する",
        context_hub_ref=ContextHubProjectRef(
            context_hub_project_id=CONTEXT_HUB_PROJECT_ID,
            api_endpoint=BASE_URL,
        ),
    )
    await project_repo.save(project)
    project_id = str(project.project_id)

    for ext_id, name in (("user-a", "メンバーA"), ("user-b", "メンバーB")):
        await member_repo.save(
            Member(
                member_id=MemberId.generate(),
                external_id=ext_id,
                name=name,
                role=MemberRole.DEVELOPER,
            )
        )

    plan = PlanService(project_repo, hub, llm, audit_repo)
    assign = AssignService(project_repo, member_repo, llm, audit_repo)
    track = TrackService(
        project_repo, member_repo, report_repo, llm,
        notifier=notifier, default_channel="#ai-pm-demo", audit_repository=audit_repo,
    )
    alert = AlertService(
        project_repo, member_repo, alert_repo, report_repo, llm,
        notifier=notifier, alert_channel="#ai-pm-alerts", audit_repository=audit_repo,
    )
    overview = OverviewService(project_repo, member_repo, alert_repo, report_repo, llm)

    # 1a. PLAN (meeting) — 会議メモ → タスク自動生成. Context-Hub extracts tasks
    # from the meeting transcript at ingestion (on-prem LLM) and AI-PM imports them.
    _hr("1a. PLAN（Context-Hub の会議メモ → 自動タスク生成）")
    meeting_res = await plan.extract_tasks_from_meeting(
        project_id=project_id, meeting_id=MEETING_ID
    )
    print(f"  会議由来 tasks_added={meeting_res.tasks_added} ids={meeting_res.task_ids}")

    # 1b. PLAN (issues) — pull real issues from live Context-Hub and convert to tasks.
    _hr("1b. PLAN（Context-Hub の実 Issue → Task）")
    total_imported = meeting_res.tasks_added
    for status in ("open", "in_progress"):
        res = await plan.import_tasks_from_issues(
            project_id=project_id, source="backlog", status_filter=status
        )
        total_imported += res.tasks_added
        print(f"  status={status}: tasks_added={res.tasks_added} ids={res.task_ids}")
    saved = await project_repo.find_by_id(project.project_id)
    print(f"  → Project 内 Task 合計: {len(saved.tasks)} 件（会議 + live CH issues 由来）")

    # 2. ASSIGN — generate draft assignments and confirm them.
    _hr("2. ASSIGN（Task → Member 割当案 → 承認）")
    drafts = await assign.generate_drafts(project_id=project_id)
    print(f"  drafts: {drafts.assignments_created} 件 / skipped: {len(drafts.skipped_task_ids)}")
    for assignment_id in drafts.assignment_ids:
        decision = await assign.confirm_assignment(
            project_id=project_id, assignment_id=assignment_id, confirmed_by="PM"
        )
        print(f"  confirm {assignment_id[:8]}… → {decision.new_status}")

    # 3. TRACK — daily report templates -> deliver -> submit responses -> analyze.
    _hr("3. TRACK（日報テンプレ → 配信 → 回答 → 分析）")
    templates = await track.generate_daily_report_templates(project_id=project_id)
    print(f"  templates: {templates.reports_generated} 件 (date={templates.report_date})")
    delivered = await track.deliver_reports(project_id=project_id)
    print(f"  delivered: {delivered.reports_delivered} / notif sent={delivered.notifications_sent}")
    if templates.report_ids:
        rid = templates.report_ids[0]
        import uuid as _uuid

        from src.domain.reporting.value_objects import DailyReportId

        report = await report_repo.find_by_id(DailyReportId(value=_uuid.UUID(rid)))
        answers = [
            ResponseInput(question_id=str(q.question_id.value), response_text="認証API設計は順調")
            for q in report.template.questions
        ]
        submitted = await track.submit_responses(report_id=rid, responses=answers)
        print(f"  submit_responses: saved={submitted.responses_saved} complete={submitted.is_complete}")
        analysis = await track.analyze_responses(report_id=rid)
        print(f"  analyze: summary={analysis.ai_summary!r} blockers={analysis.blockers_detected}")

    # 4. ALERT — scan for delays / overload / no-response (scan_date past due dates).
    _hr(f"4. ALERT（スキャン日={SCAN_DATE} で遅延検知）")
    scan = await alert.scan_project(project_id=project_id, scan_date=SCAN_DATE)
    print(f"  alerts_created: {scan.alerts_created} categories={scan.alert_categories}")
    for s in notifier.sent:
        print(f"  notifier[{s.kind}] -> {s.channel}")

    # 5. OVERVIEW — daily summary + phase progress narrative.
    _hr("5. OVERVIEW（経営サマリ）")
    summary = await overview.generate_daily_summary(project_id=project_id, summary_date=SCAN_DATE)
    print(f"  daily summary ({summary.summary_date}):")
    print(f"    tasks: {summary.task_summary}")
    print(f"    reports: {summary.report_summary}")
    print(f"    alerts: {summary.alert_summary}")
    print(f"    narrative: {summary.ai_narrative!r}")
    phases = await overview.generate_phase_progress(project_id=project_id)
    print(f"  phase progress: {len(phases.phases)} phases, overall={phases.overall_completion_rate}")

    _hr("DEMO COMPLETE")
    print(f"  imported_tasks={total_imported} assignments={drafts.assignments_created} "
          f"reports={templates.reports_generated} alerts={scan.alerts_created}")


if __name__ == "__main__":
    asyncio.run(main())

# Task 4 review package

## Scope

diff --git a/backend/app/domain/chapter_orchestration.py b/backend/app/domain/chapter_orchestration.py
index 4a1c0ee..d86c544 100644
--- a/backend/app/domain/chapter_orchestration.py
+++ b/backend/app/domain/chapter_orchestration.py
@@ -30,6 +30,9 @@ from sqlalchemy.orm import Session
 from ..db.models import (
     Chapter,
     ChapterHandoff,
+    ChapterPlanRevision,
+    ChapterPlanRevisionLink,
+    ChapterPlanSceneLink,
     ChapterRevision,
     Scene,
 )
@@ -127,6 +130,22 @@ def valid_entry_handoff(
 
 
 def _scene_ids(session: Session, chapter_id: str) -> list[str]:
+    accepted_link = session.execute(
+        select(ChapterPlanRevisionLink)
+        .join(ChapterPlanRevision, ChapterPlanRevision.id == ChapterPlanRevisionLink.plan_revision_id)
+        .where(ChapterPlanRevisionLink.chapter_id == chapter_id, ChapterPlanRevision.status == "accepted")
+    ).scalar_one_or_none()
+    if accepted_link is not None:
+        planned_scene_ids = [
+            row[0]
+            for row in session.execute(
+                select(ChapterPlanSceneLink.scene_id)
+                .where(ChapterPlanSceneLink.plan_revision_id == accepted_link.plan_revision_id)
+                .order_by(ChapterPlanSceneLink.sort_order)
+            ).all()
+        ]
+        if planned_scene_ids:
+            return planned_scene_ids
     return [
         row[0]
         for row in session.execute(
diff --git a/backend/app/domain/chapters.py b/backend/app/domain/chapters.py
index 8e2f326..ebe383a 100644
--- a/backend/app/domain/chapters.py
+++ b/backend/app/domain/chapters.py
@@ -5,8 +5,12 @@ from sqlalchemy.orm import Session
 
 from ..db.models import (
     Chapter,
+    ChapterPlanDiscussionMessage,
+    ChapterPlanProposal,
+    ChapterPlanQuestion,
     ChapterPlanRevision,
     ChapterPlanRevisionLink,
+    ChapterPlanSceneLink,
     ChapterRevision,
     ChapterRevisionScene,
     GenerationRun,
@@ -70,23 +74,214 @@ def create_chapter_plan_revision(
     return plan
 
 
+def persist_chapter_plan_candidate(
+    session: Session,
+    chapter_id: str,
+    *,
+    source_run_id: str,
+    planning_lineage_id: str,
+    chapter_contract: dict,
+    scene_briefs: list[dict],
+    reason: str,
+    contract_field_provenance: dict | None = None,
+    unresolved_assumptions: list[str] | None = None,
+    ctx: CommandContext,
+) -> ChapterPlanRevision:
+    """幂等保存 Planner 候选；候选始终保持 pending，不能绕过作者接受。"""
+    chapter = session.get(Chapter, chapter_id)
+    if chapter is None:
+        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
+    existing = session.execute(
+        select(ChapterPlanRevision)
+        .where(ChapterPlanRevision.source_run_id == source_run_id)
+        .with_for_update()
+    ).scalar_one_or_none()
+    if existing is not None:
+        if existing.chapter_id != chapter_id:
+            raise AppError("PLAN_REVISION_CONFLICT", "source run belongs to another chapter")
+        return existing
+    previous = session.execute(
+        select(ChapterPlanRevision)
+        .where(ChapterPlanRevision.planning_lineage_id == planning_lineage_id)
+        .order_by(ChapterPlanRevision.candidate_version.desc())
+        .limit(1)
+    ).scalar_one_or_none()
+    candidate_version = (previous.candidate_version + 1) if previous else 1
+    normalized_briefs = list(scene_briefs)
+    contract = dict(chapter_contract or {})
+    contract["scenes"] = normalized_briefs
+    plan = ChapterPlanRevision(
+        chapter_id=chapter_id,
+        parent_plan_revision_id=previous.id if previous else None,
+        chapter_contract=contract,
+        reason=reason,
+        status="pending",
+        plan_version=1,
+        candidate_version=candidate_version,
+        planning_lineage_id=planning_lineage_id,
+        source_run_id=source_run_id,
+        contract_field_provenance=contract_field_provenance or {},
+        scene_briefs=normalized_briefs,
+        unresolved_assumptions=unresolved_assumptions or [],
+        idempotency_key=ctx.get("idempotency_key"),
+    )
+    session.add(plan)
+    session.flush()
+    return plan
+
+
+def append_plan_discussion_message(
+    session: Session,
+    chapter_id: str,
+    planning_lineage_id: str,
+    *,
+    role: str,
+    kind: str,
+    text: str,
+    agent: str | None = None,
+    source_run_id: str | None = None,
+    parent_run_id: str | None = None,
+    supersedes_run_id: str | None = None,
+    checkpoint_id: str | None = None,
+) -> ChapterPlanDiscussionMessage:
+    """按规划血缘分配单调消息序号并持久化正文。"""
+    last = session.execute(
+        select(ChapterPlanDiscussionMessage)
+        .where(ChapterPlanDiscussionMessage.planning_lineage_id == planning_lineage_id)
+        .order_by(ChapterPlanDiscussionMessage.message_sequence.desc())
+        .limit(1)
+        .with_for_update()
+    ).scalar_one_or_none()
+    message = ChapterPlanDiscussionMessage(
+        chapter_id=chapter_id,
+        planning_lineage_id=planning_lineage_id,
+        message_sequence=(last.message_sequence + 1) if last else 1,
+        role=role,
+        agent=agent,
+        kind=kind,
+        text=text,
+        source_run_id=source_run_id,
+        parent_run_id=parent_run_id,
+        supersedes_run_id=supersedes_run_id,
+        checkpoint_id=checkpoint_id,
+    )
+    session.add(message)
+    session.flush()
+    return message
+
+
+def upsert_plan_questions(
+    session: Session,
+    chapter_id: str,
+    planning_lineage_id: str,
+    questions: list[dict],
+    source_run_id: str | None = None,
+) -> list[ChapterPlanQuestion]:
+    """保存 Planner 问题；有稳定 question_id 时反馈重放复用原记录。"""
+    result: list[ChapterPlanQuestion] = []
+    for item in questions:
+        qid = item.get("question_id")
+        question = session.get(ChapterPlanQuestion, qid) if qid else None
+        if question is None and source_run_id:
+            question = session.execute(
+                select(ChapterPlanQuestion)
+                .where(
+                    ChapterPlanQuestion.planning_lineage_id == planning_lineage_id,
+                    ChapterPlanQuestion.source_run_id == source_run_id,
+                    ChapterPlanQuestion.text == item.get("text", item.get("question", "")),
+                )
+                .limit(1)
+            ).scalar_one_or_none()
+        if question is None:
+            question = ChapterPlanQuestion(
+                question_id=qid or None,
+                chapter_id=chapter_id,
+                planning_lineage_id=planning_lineage_id,
+                text=item.get("text", item.get("question", "")),
+                impact=item.get("impact", ""),
+                status=item.get("status", "pending"),
+                source_run_id=source_run_id,
+            )
+            session.add(question)
+        else:
+            if question.planning_lineage_id != planning_lineage_id:
+                raise AppError("PLAN_REVISION_CONFLICT", "question belongs to another planning lineage")
+        result.append(question)
+    session.flush()
+    return result
+
+
+def upsert_plan_proposals(
+    session: Session,
+    chapter_id: str,
+    planning_lineage_id: str,
+    proposals: list[dict],
+    source_run_id: str | None = None,
+) -> list[ChapterPlanProposal]:
+    """保存 Planner 建议及稳定 proposal_id。"""
+    result: list[ChapterPlanProposal] = []
+    for item in proposals:
+        pid = item.get("proposal_id")
+        proposal = session.get(ChapterPlanProposal, pid) if pid else None
+        if proposal is None and source_run_id:
+            proposal = session.execute(
+                select(ChapterPlanProposal)
+                .where(
+                    ChapterPlanProposal.planning_lineage_id == planning_lineage_id,
+                    ChapterPlanProposal.source_run_id == source_run_id,
+                    ChapterPlanProposal.field_path == item.get("field_path", ""),
+                )
+                .limit(1)
+            ).scalar_one_or_none()
+        if proposal is None:
+            proposal = ChapterPlanProposal(
+                proposal_id=pid or None,
+                chapter_id=chapter_id,
+                planning_lineage_id=planning_lineage_id,
+                field_path=item.get("field_path", ""),
+                value=item.get("value", {}),
+                source=item.get("source", "ai"),
+                status=item.get("status", "pending"),
+                rationale=item.get("rationale", ""),
+                source_run_id=source_run_id,
+            )
+            session.add(proposal)
+        elif proposal.planning_lineage_id != planning_lineage_id:
+            raise AppError("PLAN_REVISION_CONFLICT", "proposal belongs to another planning lineage")
+        result.append(proposal)
+    session.flush()
+    return result
+
+
 def accept_chapter_plan_revision(
     session: Session,
     chapter_id: str,
     plan_revision_id: str,
-    expected_current_plan_revision_id: str,
+    expected_current_plan_revision_id: str | None,
     expected_plan_version: int,
     ctx: CommandContext,
 ) -> ChapterPlanRevision:
     """CAS-accept a plan revision; the current pointer must match expectations."""
-    plan = session.get(ChapterPlanRevision, plan_revision_id)
+    plan = session.execute(
+        select(ChapterPlanRevision)
+        .where(ChapterPlanRevision.id == plan_revision_id)
+        .with_for_update()
+    ).scalar_one_or_none()
     if plan is None or plan.chapter_id != chapter_id:
         raise AppError("PLAN_REVISION_CONFLICT", "plan revision does not belong to the chapter")
-    if plan.status == "accepted":
-        return plan  # idempotent accept
     link = session.execute(
-        select(ChapterPlanRevisionLink).where(ChapterPlanRevisionLink.chapter_id == chapter_id)
+        select(ChapterPlanRevisionLink)
+        .where(ChapterPlanRevisionLink.chapter_id == chapter_id)
+        .with_for_update()
     ).scalar_one_or_none()
+    if plan.status == "accepted":
+        if link is None or link.plan_revision_id != plan.id:
+            raise AppError("PLAN_REVISION_CONFLICT", "accepted plan pointer is inconsistent")
+        if expected_current_plan_revision_id != link.plan_revision_id:
+            raise AppError("PLAN_REVISION_CONFLICT", "current plan revision pointer mismatch")
+        if expected_plan_version != link.plan_version:
+            raise AppError("PLAN_REVISION_CONFLICT", "expected plan version mismatch")
+        return plan
     if link is None:
         if expected_current_plan_revision_id is not None:
             raise AppError("PLAN_REVISION_CONFLICT", "no current plan revision exists")
@@ -99,6 +294,21 @@ def accept_chapter_plan_revision(
             raise AppError("PLAN_REVISION_CONFLICT", "expected plan version mismatch")
         plan.plan_version = link.plan_version + 1
 
+    provenance = plan.contract_field_provenance or {}
+    unresolved = [
+        path for path, value in provenance.items()
+        if isinstance(value, dict) and value.get("status") in {"ai_suggested", "unresolved"}
+    ]
+    for index, brief in enumerate(plan.scene_briefs or []):
+        for path, value in (brief.get("field_provenance") or {}).items():
+            if isinstance(value, dict) and value.get("status") in {"ai_suggested", "unresolved"}:
+                unresolved.append(f"scene_briefs[{index}].{path}")
+    if unresolved:
+        raise AppError(
+            "PLAN_NOT_ACCEPTED",
+            "candidate contains unconfirmed fields",
+            details={"field_paths": unresolved},
+        )
     plan.status = "accepted"
     if link is None:
         session.add(
@@ -112,6 +322,11 @@ def accept_chapter_plan_revision(
         link.plan_revision_id = plan.id
         link.plan_version = plan.plan_version
     session.flush()
+    # 接受命令在同一事务内固定场景映射并准备 outbox。
+    # 兼容初始化计划可能只有 chapter_contract.scenes，因此这里保留该回退。
+    scene_specs = plan.scene_briefs or (plan.chapter_contract or {}).get("scenes") or []
+    if scene_specs:
+        materialize_chapter_plan(session, chapter_id, plan.id, scene_specs, ctx)
     return plan
 
 
@@ -131,26 +346,285 @@ def materialize_chapter_plan(
         raise AppError("PLAN_NOT_ACCEPTED", "plan must be accepted before materialization")
 
     mapping: dict[str, str] = {}
-    for spec in scene_specs:
+    existing_links = {
+        row.client_key: row
+        for row in session.execute(
+            select(ChapterPlanSceneLink).where(ChapterPlanSceneLink.plan_revision_id == plan_revision_id)
+        ).scalars()
+    }
+    for sort_order, spec in enumerate(scene_specs):
         client_key = spec.get("client_key")
         if not client_key:
             raise AppError("PLAN_REVISION_CONFLICT", "scene spec requires a client_key")
         if client_key in mapping:
             raise AppError("PLAN_REVISION_CONFLICT", "duplicate client_key in scene specs")
-        if spec.get("scene_id"):
-            mapping[client_key] = spec["scene_id"]
+        if client_key in existing_links:
+            mapping[client_key] = existing_links[client_key].scene_id
             continue
-        scene = Scene(
-            chapter_id=chapter_id,
-            title=spec.get("title", client_key),
-            scene_brief=spec.get("scene_brief", {}),
-        )
-        session.add(scene)
-        session.flush()
+        scene_id = spec.get("scene_id")
+        scene = session.get(Scene, scene_id) if scene_id else None
+        if scene is None:
+            scene = Scene(
+                chapter_id=chapter_id,
+                title=spec.get("title", client_key),
+                scene_brief=spec.get("scene_brief", spec.get("brief", {})),
+            )
+            session.add(scene)
+            session.flush()
+        elif scene.chapter_id != chapter_id:
+            raise AppError("PLAN_REVISION_CONFLICT", "scene does not belong to chapter")
         mapping[client_key] = scene.id
+        session.add(
+            ChapterPlanSceneLink(
+                chapter_id=chapter_id,
+                plan_revision_id=plan_revision_id,
+                client_key=client_key,
+                scene_id=scene.id,
+                sort_order=sort_order,
+            )
+        )
+    # accepted pointer 与场景映射在同一事务中发出，消费者只按固定映射入队。
+    from ..runtime.outbox import PostgresRunOutbox
+
+    producer = ctx.get("manual_command_id") or ctx.get("idempotency_key") or "chapter-plan-accept"
+    run_id = ctx.get("generation_run_id")
+    if run_id is not None and session.get(GenerationRun, run_id) is None:
+        run_id = None
+    PostgresRunOutbox(session).enqueue(
+        {
+            "resource_type": "chapter_plan",
+            "resource_id": plan_revision_id,
+            "payload_schema": "chapter-plan.v1",
+            "payload": {
+                "event_type": "chapter_plan.accepted",
+                "chapter_id": chapter_id,
+                "plan_revision_id": plan_revision_id,
+                "scene_mapping": mapping,
+            },
+            "producer_command_id": producer,
+            "generation_run_id": run_id,
+        },
+        fencing_token=(ctx.get("write_fence") or {}).get("fencing_token", 0),
+    )
     return mapping
 
 
+def chapter_workflow_read(session: Session, chapter_id: str) -> dict:
+    """组合读取章节规划、讨论、固定场景映射和活动运行状态。"""
+    chapter = session.get(Chapter, chapter_id)
+    if chapter is None:
+        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
+    accepted_link = session.execute(
+        select(ChapterPlanRevisionLink).where(ChapterPlanRevisionLink.chapter_id == chapter_id)
+    ).scalar_one_or_none()
+    accepted = session.get(ChapterPlanRevision, accepted_link.plan_revision_id) if accepted_link else None
+    blocking: list[str] = []
+    if accepted_link is not None and accepted is None:
+        blocking.append(f"accepted_plan_missing:{accepted_link.plan_revision_id}")
+    elif accepted_link is not None and accepted.status != "accepted":
+        blocking.append(f"accepted_plan_not_accepted:{accepted.id}")
+    candidate = session.execute(
+        select(ChapterPlanRevision)
+        .where(ChapterPlanRevision.chapter_id == chapter_id, ChapterPlanRevision.status == "pending")
+        .order_by(ChapterPlanRevision.candidate_version.desc(), ChapterPlanRevision.created_at.desc())
+        .limit(1)
+    ).scalar_one_or_none()
+    lineage = (candidate or accepted)
+    lineage_id = lineage.planning_lineage_id if lineage and lineage.planning_lineage_id else chapter_id
+
+    messages = session.execute(
+        select(ChapterPlanDiscussionMessage)
+        .where(ChapterPlanDiscussionMessage.planning_lineage_id == lineage_id)
+        .order_by(ChapterPlanDiscussionMessage.message_sequence)
+    ).scalars().all()
+    questions = session.execute(
+        select(ChapterPlanQuestion)
+        .where(ChapterPlanQuestion.planning_lineage_id == lineage_id, ChapterPlanQuestion.status == "pending")
+        .order_by(ChapterPlanQuestion.created_at)
+    ).scalars().all()
+    proposals = session.execute(
+        select(ChapterPlanProposal)
+        .where(ChapterPlanProposal.planning_lineage_id == lineage_id)
+        .order_by(ChapterPlanProposal.created_at)
+    ).scalars().all()
+    runs = session.execute(
+        select(GenerationRun)
+        .where(GenerationRun.chapter_id == chapter_id)
+        .where(GenerationRun.status.not_in(("accepted", "cancelled", "failed", "superseded")))
+        .order_by(GenerationRun.created_at.desc())
+    ).scalars().all()
+    if len(runs) > 1:
+        blocking.append("multiple_active_runs")
+    active = runs[0] if len(runs) == 1 else None
+    run_snapshot = None
+    if active is not None:
+        run_snapshot = {
+            "run_id": active.id,
+            "thread_id": active.id,
+            "project_id": active.project_id,
+            "target_id": active.scene_id or active.chapter_id or "",
+            "run_scope": "scene" if active.scene_id else "chapter",
+            "request_type": active.request_type or "continue",
+            "plan_revision_id": active.plan_revision_id,
+            "base_scene_revision_id": (active.normalized_input or {}).get("base_scene_revision_id"),
+            "status": active.status,
+            "run_version": active.run_version,
+            "current_scene_id": active.scene_id,
+            "current_node": active.last_durable_node,
+            "pending_node": active.pending_node,
+            "pause_reason": active.pause_reason,
+            "clarification_questions": active.clarification_questions or [],
+            "last_error_code": active.last_error_code,
+            "decision_target": active.decision_target,
+        }
+    links = []
+    if accepted is not None:
+        links = session.execute(
+            select(ChapterPlanSceneLink)
+            .where(ChapterPlanSceneLink.plan_revision_id == accepted.id)
+            .order_by(ChapterPlanSceneLink.sort_order)
+        ).scalars().all()
+        linked_keys = {link.client_key for link in links}
+        for brief in accepted.scene_briefs or []:
+            client_key = brief.get("client_key")
+            if client_key and client_key not in linked_keys:
+                blocking.append(f"scene_mapping_missing:{client_key}")
+    scene_rows = {s.id: s for s in session.execute(select(Scene).where(Scene.chapter_id == chapter_id)).scalars().all()}
+    scene_views = []
+    for link_index, link in enumerate(links):
+        scene = scene_rows.get(link.scene_id)
+        if scene is None:
+            blocking.append(f"scene_missing:{link.scene_id}")
+            continue
+        scene_run = session.execute(
+            select(GenerationRun)
+            .where(GenerationRun.scene_id == scene.id)
+            .where(GenerationRun.plan_revision_id == (accepted.id if accepted else None))
+            .where(GenerationRun.status.not_in(("accepted", "cancelled", "failed", "superseded")))
+            .order_by(GenerationRun.created_at.desc())
+            .limit(1)
+        ).scalar_one_or_none()
+        scene_blocking: list[str] = []
+        if link_index > 0:
+            previous_links = links[:link_index]
+            if any(scene_rows.get(prev.scene_id) is None or scene_rows[prev.scene_id].accepted_scene_revision_id is None for prev in previous_links):
+                scene_blocking.append("previous_scene_not_accepted")
+        if scene_run is not None and scene_run.plan_revision_id != accepted.id:
+            scene_blocking.append("scene_plan_mismatch")
+        for reason in scene_blocking:
+            blocking.append(f"scene_blocked:{scene.id}:{reason}")
+        scene_views.append(
+            {
+                "scene_id": scene.id,
+                "order": link.sort_order,
+                "title": scene.title,
+                "status": ("accepted" if scene.accepted_scene_revision_id else (scene_run.status if scene_run else "planned")),
+                "accepted_revision_id": scene.accepted_scene_revision_id,
+                "current_run_id": scene_run.id if scene_run else None,
+                "blocking_reasons": scene_blocking,
+            }
+        )
+    if blocking:
+        phase = "blocked"
+    elif active is not None and active.decision_target == "plan":
+        phase = "plan_feedback" if active.status in ("waiting_feedback", "pending_clarification") else "planning"
+    elif accepted is None:
+        phase = "intent_required" if not (chapter.chapter_intent or {}).get("text", "").strip() else "planning"
+    elif any(v["status"] in {"waiting_feedback", "pending_clarification"} for v in scene_views):
+        phase = "scene_feedback"
+    elif scene_views and any(v["status"] != "accepted" for v in scene_views):
+        phase = "scene_generation"
+    else:
+        phase = "completed" if chapter.accepted_chapter_revision_id else "chapter_review"
+    pending_decision = {"target": None, "kind": None, "run_id": None, "expected_run_version": None}
+    if active is not None:
+        target = active.decision_target
+        pending_decision.update(
+            {
+                "target": target,
+                "run_id": active.id,
+                "expected_run_version": active.run_version,
+                "kind": "accept_plan" if target == "plan" and active.status == "waiting_feedback" else (
+                    "answer_planner" if target == "plan" else "answer_scene" if target == "scene" else "chapter_feedback"
+                ),
+            }
+        )
+    elif candidate is not None:
+        pending_decision["kind"] = "accept_plan"
+        pending_decision["target"] = "plan"
+    return {
+        "chapter_id": chapter_id,
+        "phase": phase,
+        "chapter_status": chapter.chapter_sync_status or "draft",
+        "pending_decision": pending_decision,
+        "intent": {
+            "text": (chapter.chapter_intent or {}).get("text", ""),
+            "optional_fields": chapter.chapter_intent or {},
+            "unresolved_questions": [q.text for q in questions],
+        },
+        "plan_discussion": {
+            "messages": [
+                {
+                    "message_id": m.message_id,
+                    "message_sequence": m.message_sequence,
+                    "role": m.role,
+                    "agent": m.agent,
+                    "kind": m.kind,
+                    "text": m.text,
+                    "created_at": m.created_at.isoformat(),
+                    "source_run_id": m.source_run_id,
+                    "parent_run_id": m.parent_run_id,
+                    "supersedes_run_id": m.supersedes_run_id,
+                    "checkpoint_id": m.checkpoint_id,
+                }
+                for m in messages
+            ],
+            "pending_questions": [
+                {"question_id": q.question_id, "text": q.text, "impact": q.impact} for q in questions
+            ],
+            "pending_proposals": [
+                {
+                    "proposal_id": p.proposal_id,
+                    "field_path": p.field_path,
+                    "value": p.value,
+                    "source": p.source,
+                    "status": p.status,
+                    "rationale": p.rationale,
+                }
+                for p in proposals
+                if p.status == "pending"
+            ],
+        },
+        "plan": {
+            "candidate_revision_id": candidate.id if candidate else None,
+            "accepted_revision_id": accepted.id if accepted else None,
+            "candidate_version": candidate.candidate_version if candidate else None,
+            "accepted_version": accepted_link.plan_version if accepted_link else None,
+            "status": "accepted" if accepted else "candidate" if candidate else "none",
+            "contract": (candidate or accepted).chapter_contract if (candidate or accepted) else None,
+            "contract_field_provenance": (candidate or accepted).contract_field_provenance if (candidate or accepted) else {},
+            "scene_briefs": [
+                {
+                    "client_key": b.get("client_key", ""),
+                    "order": i,
+                    "title": b.get("title", b.get("client_key", "")),
+                    "brief": b.get("scene_brief", b.get("brief", {})),
+                    "field_provenance": b.get("field_provenance", {}),
+                    "status": "accepted" if accepted else "proposed",
+                }
+                for i, b in enumerate((candidate or accepted).scene_briefs if (candidate or accepted) else [])
+            ],
+        },
+        "scenes": scene_views,
+        "chapter_revision": {"staged_revision_id": None, "accepted_revision_id": chapter.accepted_chapter_revision_id, "review_run_id": None},
+        "active_run": run_snapshot,
+        "affected_scene_ids": list((active.normalized_input or {}).get("affected_scene_ids", [])) if active else [],
+        "stale_scene_ids": list((active.normalized_input or {}).get("stale_scene_ids", [])) if active else [],
+        "blocking_reasons": blocking,
+        "canon_run_id": None,
+    }
+
+
 def aggregate_chapter_revision(
     session: Session,
     chapter_id: str,
diff --git a/backend/app/runtime/run_worker.py b/backend/app/runtime/run_worker.py
index 969598c..7601345 100644
--- a/backend/app/runtime/run_worker.py
+++ b/backend/app/runtime/run_worker.py
@@ -39,18 +39,27 @@ from app.agents.schemas import (
 from app.agents.state import ChapterRunState
 from app.agents.writing_agent import WritingAgent
 from app.db.models import (
+    Chapter,
+    ChapterPlanDiscussionMessage,
+    ChapterPlanProposal,
+    ChapterPlanQuestion,
     ChapterPlanRevision,
     ChapterPlanRevisionLink,
+    ChapterPlanSceneLink,
     GenerationRun,
+    RunOutboxRecord,
     Scene,
     SceneRevision,
+    Volume,
 )
 from app.errors import AppError
 from app.observability.wiring import ObservabilityWiring
 from app.runtime.executor import RunExecutor
 from app.runtime.leases import LeaseRepository
+from app.runtime.outbox import PostgresRunOutbox
 from app.runtime.run_events import PostgresRunEventStore
 from app.runtime.run_identity import RunIdentity
+from app.services.generation_runs import persist_planner_output
 
 # 图构造器：给定运行与会话返回可执行图（默认按运行类型选择三图之一）。
 GraphBuilder = Callable[[GenerationRun, Session], Any]
@@ -94,6 +103,10 @@ class RunWorker:
         返回：本次实际处理并提交的运行数量；无 queued 运行时返回 0。
         约束：并发 worker 通过行锁领取，同一运行只被一个 worker 处理一次。
         """
+        # 先恢复 accepted plan 产生的场景队列；tick 返回值仍表示实际执行的运行数。
+        self._consume_plan_outbox()
+        # outbox 只负责首次投递；后续场景由 accepted 状态恢复，避免 outbox 已消费后队列停滞。
+        self._recover_accepted_plan_scene_queues()
         processed = 0
         while True:
             run_id = self._peek_queued()
@@ -106,6 +119,227 @@ class RunWorker:
             processed += 1
         return processed
 
+    def _recover_accepted_plan_scene_queues(self) -> int:
+        """恢复 accepted plan 的下一个场景运行，严格保持计划顺序并可重复执行。"""
+        created = 0
+        with self._factory() as session:
+            accepted_plans = session.execute(
+                select(ChapterPlanRevisionLink)
+                .order_by(ChapterPlanRevisionLink.chapter_id, ChapterPlanRevisionLink.plan_revision_id)
+                .with_for_update(skip_locked=True)
+            ).scalars().all()
+            for accepted_link in accepted_plans:
+                if self._ensure_next_scene_run(
+                    session, accepted_link.chapter_id, accepted_link.plan_revision_id
+                ):
+                    created += 1
+            session.commit()
+        return created
+
+    def _ensure_next_scene_run(self, session: Session, chapter_id: str, plan_revision_id: str) -> bool:
+        """在单个 accepted plan 下最多创建一个当前可运行场景。"""
+        links = session.execute(
+            select(ChapterPlanSceneLink)
+            .where(
+                ChapterPlanSceneLink.chapter_id == chapter_id,
+                ChapterPlanSceneLink.plan_revision_id == plan_revision_id,
+            )
+            .order_by(ChapterPlanSceneLink.sort_order)
+            .with_for_update()
+        ).scalars().all()
+        for index, scene_link in enumerate(links):
+            existing = session.execute(
+                select(GenerationRun)
+                .where(
+                    GenerationRun.chapter_id == chapter_id,
+                    GenerationRun.scene_id == scene_link.scene_id,
+                    GenerationRun.plan_revision_id == plan_revision_id,
+                )
+                .order_by(GenerationRun.created_at.desc())
+                .with_for_update()
+            ).scalars().first()
+            if existing is not None:
+                if existing.status != "accepted":
+                    return False
+                continue
+            if index > 0:
+                previous = links[index - 1]
+                previous_scene = session.get(Scene, previous.scene_id)
+                previous_run = session.execute(
+                    select(GenerationRun)
+                    .where(
+                        GenerationRun.chapter_id == chapter_id,
+                        GenerationRun.scene_id == previous.scene_id,
+                        GenerationRun.plan_revision_id == plan_revision_id,
+                    )
+                    .order_by(GenerationRun.created_at.desc())
+                    .with_for_update()
+                ).scalars().first()
+                if (
+                    previous_scene is None
+                    or previous_scene.accepted_scene_revision_id is None
+                    or previous_run is None
+                    or previous_run.status != "accepted"
+                ):
+                    return False
+            chapter = session.get(Chapter, chapter_id)
+            if chapter is None:
+                return False
+            volume = session.get(Volume, chapter.volume_id)
+            scene = session.get(Scene, scene_link.scene_id)
+            base_scene_revision_id = scene.accepted_scene_revision_id if scene is not None else None
+            run = GenerationRun(
+                project_id=volume.project_id if volume is not None else chapter.volume_id,
+                chapter_id=chapter_id,
+                scene_id=scene_link.scene_id,
+                plan_revision_id=plan_revision_id,
+                request_type="continue",
+                decision_target="scene",
+                status="queued",
+                normalized_input={
+                    "run_scope": "scene",
+                    "request_type": "continue",
+                    "decision_target": "scene",
+                    "plan_revision_id": plan_revision_id,
+                    "base_scene_revision_id": base_scene_revision_id,
+                    "chapter_intent": chapter.chapter_intent or {},
+                },
+            )
+            session.add(run)
+            session.flush()
+            PostgresRunEventStore(session).emit(
+                run.id,
+                "run_queued",
+                {
+                    "run_scope": "scene",
+                    "request_type": "continue",
+                    "plan_revision_id": plan_revision_id,
+                    "base_scene_revision_id": base_scene_revision_id,
+                },
+                fencing_token=0,
+            )
+            PostgresRunOutbox(session).enqueue(
+                {
+                    "resource_type": "run",
+                    "resource_id": run.id,
+                    "payload_schema": "run-event.v1",
+                    "payload": {"event_type": "run_queued", "run_id": run.id},
+                    "producer_command_id": run.id,
+                    "generation_run_id": run.id,
+                },
+                fencing_token=0,
+            )
+            return True
+        return False
+
+    def _consume_plan_outbox(self) -> int:
+        """消费 accepted plan 事件并恢复第一个未完成场景。
+
+        outbox 记录本身是可重放的；场景运行通过固定 `(plan_revision_id, scene_id)`
+        查重，因此 Worker 重启或重复投递不会创建第二个场景运行。
+        """
+        consumed = 0
+        with self._factory() as session:
+            rows = session.execute(
+                select(RunOutboxRecord)
+                .where(
+                    RunOutboxRecord.resource_type == "chapter_plan",
+                    RunOutboxRecord.delivery_status.in_(("pending", "publishing", "published", "consumed")),
+                )
+                .order_by(RunOutboxRecord.created_at)
+                .with_for_update(skip_locked=True)
+            ).scalars().all()
+            for record in rows:
+                payload = record.payload or {}
+                if payload.get("event_type") != "chapter_plan.accepted":
+                    continue
+                chapter_id = payload.get("chapter_id")
+                plan_revision_id = payload.get("plan_revision_id")
+                if not chapter_id or not plan_revision_id:
+                    record.delivery_status = "failed"
+                    record.last_error = "invalid chapter_plan.accepted payload"
+                    continue
+                accepted_link = session.execute(
+                    select(ChapterPlanRevisionLink)
+                    .where(ChapterPlanRevisionLink.chapter_id == chapter_id)
+                    .with_for_update()
+                ).scalar_one_or_none()
+                if accepted_link is None or accepted_link.plan_revision_id != plan_revision_id:
+                    record.delivery_status = "failed"
+                    record.last_error = "accepted plan pointer does not match outbox payload"
+                    continue
+                link = session.execute(
+                    select(ChapterPlanSceneLink)
+                    .where(ChapterPlanSceneLink.plan_revision_id == plan_revision_id)
+                    .order_by(ChapterPlanSceneLink.sort_order)
+                ).scalars().all()
+                for scene_link in link:
+                    existing_run = session.execute(
+                        select(GenerationRun)
+                        .where(
+                            GenerationRun.chapter_id == chapter_id,
+                            GenerationRun.scene_id == scene_link.scene_id,
+                            GenerationRun.plan_revision_id == plan_revision_id,
+                        )
+                        .limit(1)
+                    ).scalar_one_or_none()
+                    if existing_run is not None:
+                        # 只有已接受的场景才推进队列；暂停/运行中的首场景阻止
+                        # 后续场景物化，避免 outbox 重放制造并行场景运行。
+                        if existing_run.status != "accepted":
+                            break
+                        continue
+                    chapter = session.get(Chapter, chapter_id)
+                    if chapter is None:
+                        record.delivery_status = "failed"
+                        record.last_error = "chapter not found"
+                        break
+                    volume = session.get(Volume, chapter.volume_id)
+                    scene = session.get(Scene, scene_link.scene_id)
+                    base_scene_revision_id = scene.accepted_scene_revision_id if scene is not None else None
+                    run = GenerationRun(
+                        project_id=volume.project_id if volume is not None else chapter.volume_id,
+                        chapter_id=chapter_id,
+                        scene_id=scene_link.scene_id,
+                        plan_revision_id=plan_revision_id,
+                        request_type="continue",
+                        decision_target="scene",
+                        status="queued",
+                        normalized_input={
+                            "run_scope": "scene",
+                            "request_type": "continue",
+                            "decision_target": "scene",
+                            "plan_revision_id": plan_revision_id,
+                            "base_scene_revision_id": base_scene_revision_id,
+                            "chapter_intent": chapter.chapter_intent or {},
+                        },
+                    )
+                    session.add(run)
+                    session.flush()
+                    PostgresRunEventStore(session).emit(
+                        run.id,
+                        "run_queued",
+                        {"run_scope": "scene", "request_type": "continue", "plan_revision_id": plan_revision_id},
+                        fencing_token=0,
+                        producer_command_id=record.producer_command_id,
+                    )
+                    PostgresRunOutbox(session).enqueue(
+                        {
+                            "resource_type": "run",
+                            "resource_id": run.id,
+                            "payload_schema": "run-event.v1",
+                            "payload": {"event_type": "run_queued", "run_id": run.id},
+                            "producer_command_id": run.id,
+                            "generation_run_id": run.id,
+                        },
+                        fencing_token=0,
+                    )
+                    break
+                record.delivery_status = "consumed"
+                consumed += 1
+            session.commit()
+        return consumed
+
     def run_forever(self, interval: float = 1.0) -> None:
         """持续轮询执行（Worker 进程主循环）。
 
@@ -161,6 +395,11 @@ class RunWorker:
                 state,
                 envelope,
             )
+            planner_output = result.get("planner_output")
+            if planner_output is not None and run.chapter_id and run.decision_target == "plan":
+                from app.agents.schemas import ChapterPlanOutput
+
+                persist_planner_output(session, run.id, ChapterPlanOutput(**planner_output), actor_id=self._actor_id)
             self._persist_outcome(session, run, result)
             session.commit()
         except Exception as exc:
@@ -292,6 +531,14 @@ class RunWorker:
         绝不重新读取客户端输入。
         """
         ni = run.normalized_input or {}
+        chapter_intent = ni.get("chapter_intent") or {}
+        if run.chapter_id:
+            chapter = session.get(Chapter, run.chapter_id)
+            if chapter is not None:
+                persisted_intent = chapter.chapter_intent or {}
+                # 旧初始化数据可能只有占位键；只有包含自然语言 text 才作为 Planner 意图。
+                if not chapter_intent.get("text") and persisted_intent.get("text"):
+                    chapter_intent = persisted_intent
         scene_brief: dict = {}
         accepted_text = ""
         accepted_scene_revision_id: str | None = None
@@ -312,6 +559,36 @@ class RunWorker:
                 accepted_chapter_revision_id = run.canon_source_revision_id
         base_scene_revision_id = ni.get("base_scene_revision_id")
         chapter_contract = self._chapter_contract_for(session, run)
+        is_planner_run = run.chapter_id is not None and run.decision_target == "plan"
+        lineage = self._planning_lineage_for(session, run) if is_planner_run else None
+        discussion: list[dict] = []
+        questions: list[dict] = []
+        proposals: list[dict] = []
+        if is_planner_run and lineage:
+            discussion = [
+                {"role": row.role, "kind": row.kind, "text": row.text, "source_run_id": row.source_run_id}
+                for row in session.execute(
+                    select(ChapterPlanDiscussionMessage)
+                    .where(ChapterPlanDiscussionMessage.planning_lineage_id == lineage)
+                    .order_by(ChapterPlanDiscussionMessage.message_sequence)
+                ).scalars()
+            ]
+            questions = [
+                {"question_id": row.question_id, "text": row.text, "impact": row.impact, "status": row.status}
+                for row in session.execute(
+                    select(ChapterPlanQuestion)
+                    .where(ChapterPlanQuestion.planning_lineage_id == lineage, ChapterPlanQuestion.status == "pending")
+                    .order_by(ChapterPlanQuestion.created_at)
+                ).scalars()
+            ]
+            proposals = [
+                {"proposal_id": row.proposal_id, "field_path": row.field_path, "value": row.value, "source": row.source, "status": row.status}
+                for row in session.execute(
+                    select(ChapterPlanProposal)
+                    .where(ChapterPlanProposal.planning_lineage_id == lineage, ChapterPlanProposal.status == "pending")
+                    .order_by(ChapterPlanProposal.created_at)
+                ).scalars()
+            ]
         manifest = [
             ContextManifestEntry(source_id=rid, kind="revision", revision_id=rid)
             for rid in dict.fromkeys([base_scene_revision_id, accepted_scene_revision_id])
@@ -345,6 +622,10 @@ class RunWorker:
             accepted_scene_revision_id=accepted_scene_revision_id,
             accepted_chapter_revision_id=accepted_chapter_revision_id,
             chapter_contract=chapter_contract,
+            chapter_intent=chapter_intent if is_planner_run else {},
+            plan_discussion=discussion if is_planner_run else [],
+            pending_plan_questions=questions if is_planner_run else [],
+            pending_plan_proposals=proposals if is_planner_run else [],
             canon_scope=(
                 ("scene" if run.scene_id else "chapter")
                 if run.decision_target == "canon"
@@ -360,6 +641,23 @@ class RunWorker:
             write_fence_fencing_token=lease["fencing_token"],
         )
 
+    def _planning_lineage_for(self, session: Session, run: GenerationRun) -> str | None:
+        """从持久化计划血缘恢复 Planner 讨论上下文。"""
+        value = (run.normalized_input or {}).get("planning_lineage_id")
+        if value:
+            return str(value)
+        if run.parent_plan_revision_id:
+            parent = session.get(ChapterPlanRevision, run.parent_plan_revision_id)
+            if parent is not None and parent.planning_lineage_id:
+                return parent.planning_lineage_id
+            return run.parent_plan_revision_id
+        candidate = session.execute(
+            select(ChapterPlanRevision)
+            .where(ChapterPlanRevision.source_run_id == run.id)
+            .limit(1)
+        ).scalar_one_or_none()
+        return candidate.planning_lineage_id if candidate else run.id
+
     def _chapter_contract_for(self, session: Session, run: GenerationRun) -> dict:
         """由已接受章节计划修订源取章节契约（供 ChapterPlanner/Review 使用）。
 
diff --git a/backend/app/services/generation_runs.py b/backend/app/services/generation_runs.py
index 1e0f8f4..387cfaa 100644
--- a/backend/app/services/generation_runs.py
+++ b/backend/app/services/generation_runs.py
@@ -18,6 +18,7 @@ from typing import Any, Literal
 from sqlalchemy import select
 from sqlalchemy.orm import Session
 
+from ..agents.schemas import ChapterPlanOutput
 from ..api.schemas import (
     DecisionRequest,
     ResumeRequest,
@@ -26,16 +27,24 @@ from ..api.schemas import (
 )
 from ..db.models import (
     Chapter,
+    ChapterPlanDiscussionMessage,
+    ChapterPlanProposal,
+    ChapterPlanQuestion,
     ChapterPlanRevision,
     ChapterPlanRevisionLink,
+    ChapterPlanSceneLink,
     GenerationRun,
     Scene,
     Volume,
 )
+from ..domain.chapter_orchestration import build_scene_feedback_queue
 from ..domain.chapters import (
     accept_chapter_plan_revision,
+    append_plan_discussion_message,
     commit_chapter_version,
-    materialize_chapter_plan,
+    persist_chapter_plan_candidate,
+    upsert_plan_proposals,
+    upsert_plan_questions,
 )
 from ..domain.commit_guard import CommitGuard
 from ..domain.drafts import commit_scene_draft
@@ -254,8 +263,27 @@ def _validate_run_create(
             )
         if plan.status != "accepted":
             raise AppError("PLAN_REVISION_CONFLICT", "plan revision is not accepted")
+        if scene is not None:
+            scene_link = session.execute(
+                select(ChapterPlanSceneLink).where(
+                    ChapterPlanSceneLink.plan_revision_id == body.plan_revision_id,
+                    ChapterPlanSceneLink.scene_id == scene.id,
+                )
+            ).scalar_one_or_none()
+            if scene_link is None:
+                raise AppError(
+                    "SCENE_PLAN_MISMATCH",
+                    "scene is not part of the current accepted plan",
+                )
     # 严格校验跨章节入口（首章/紧邻前一章/来源 accepted 版本/交接匹配）。
     _validate_cross_chapter_entry(session, chapter, body)
+    if body.plan_revision_id is None and body.request_type == "new_chapter":
+        intent = body.chapter_intent or {}
+        if not isinstance(intent, dict) or not str(intent.get("text", "")).strip():
+            raise AppError(
+                "COMMAND_CONTEXT_MISMATCH",
+                "new chapter planning requires non-empty chapter_intent.text",
+            )
     # 场景基线：已有 accepted 版本时必须匹配；首次生成不允许携带基线。
     if scene is not None:
         if scene.accepted_scene_revision_id is not None:
@@ -271,6 +299,32 @@ def _validate_run_create(
             raise AppError("CHAPTER_OUT_OF_SYNC", "chapter baseline is out of sync")
 
 
+def _validate_scene_queue_position(session: Session, run: GenerationRun) -> None:
+    """校验场景决策只能作用于当前 accepted plan 的队首场景。"""
+    if run.scene_id is None or run.plan_revision_id is None:
+        return
+    links = session.execute(
+        select(ChapterPlanSceneLink)
+        .where(ChapterPlanSceneLink.plan_revision_id == run.plan_revision_id)
+        .order_by(ChapterPlanSceneLink.sort_order)
+    ).scalars().all()
+    current_index = next((i for i, link in enumerate(links) if link.scene_id == run.scene_id), None)
+    if current_index is None:
+        raise AppError("SCENE_PLAN_MISMATCH", "scene is not part of the current accepted plan")
+    for previous in links[:current_index]:
+        scene = session.get(Scene, previous.scene_id)
+        previous_run = session.execute(
+            select(GenerationRun)
+            .where(
+                GenerationRun.scene_id == previous.scene_id,
+                GenerationRun.plan_revision_id == run.plan_revision_id,
+            )
+            .order_by(GenerationRun.created_at.desc())
+        ).scalars().first()
+        if scene is None or scene.accepted_scene_revision_id is None or previous_run is None or previous_run.status != "accepted":
+            raise AppError("SCENE_QUEUE_BLOCKED", "previous scene must be accepted before this scene")
+
+
 def start_generation_run(
     session: Session,
     actor_id: str,
@@ -400,6 +454,84 @@ def get_run_input_envelope(session: Session, run_id: str) -> dict:
     return run.normalized_input
 
 
+def persist_planner_output(
+    session: Session,
+    run_id: str,
+    output: ChapterPlanOutput,
+    *,
+    actor_id: str = "planner",
+) -> ChapterPlanRevision | None:
+    """在 Planner 节点成功后持久化候选、讨论问题和建议，支持节点重试幂等。"""
+    run = get_run(session, run_id)
+    if run.chapter_id is None:
+        raise AppError("RUN_STATE_CONFLICT", "planner run has no chapter target")
+    lineage = run.parent_plan_revision_id or run.id
+    # 同一 source_run_id 的候选由领域服务锁定并返回，不重复产生语义版本。
+    plan = None
+    scene_briefs = []
+    for scene in output.scene_contracts:
+        brief = dict(scene)
+        key = brief.get("client_key", "")
+        scene_field_provenance = getattr(output, "scene_field_provenance", {})
+        if key in scene_field_provenance:
+            brief["field_provenance"] = scene_field_provenance[key]
+        scene_briefs.append(brief)
+    if output.status == "ready":
+        plan = persist_chapter_plan_candidate(
+            session,
+            run.chapter_id,
+            source_run_id=run.id,
+            planning_lineage_id=lineage,
+            chapter_contract=output.chapter_contract,
+            scene_briefs=scene_briefs,
+            reason=output.reason or "planner-candidate",
+            contract_field_provenance=getattr(output, "contract_field_provenance", {}),
+            unresolved_assumptions=getattr(output, "unresolved_assumptions", []),
+            ctx={
+                "actor_id": actor_id,
+                "idempotency_key": run.id,
+                "source": "agent",
+                "generation_run_id": run.id,
+            },
+        )
+    # source_run_id 唯一检查避免同一节点重试重复写消息。
+    existing_message = session.execute(
+        select(ChapterPlanDiscussionMessage).where(
+            ChapterPlanDiscussionMessage.planning_lineage_id == lineage,
+            ChapterPlanDiscussionMessage.source_run_id == run.id,
+        )
+    ).scalar_one_or_none()
+    if existing_message is None:
+        text = output.reason or ("; ".join(output.clarification_questions) if output.clarification_questions else "Planner candidate")
+        append_plan_discussion_message(
+            session,
+            run.chapter_id,
+            lineage,
+            role="planner",
+            agent="ChapterPlannerAgent",
+            kind="proposal" if output.status == "ready" else "question",
+            text=text,
+            source_run_id=run.id,
+            parent_run_id=run.parent_generation_run_id,
+            supersedes_run_id=run.supersedes_run_id,
+        )
+    upsert_plan_questions(
+        session,
+        run.chapter_id,
+        lineage,
+        [{"text": q, "impact": "planner clarification"} for q in output.clarification_questions],
+        source_run_id=run.id,
+    )
+    upsert_plan_proposals(
+        session,
+        run.chapter_id,
+        lineage,
+        getattr(output, "proposals", []),
+        source_run_id=run.id,
+    )
+    return plan
+
+
 def run_snapshot(session: Session, run_id: str) -> dict:
     """构造 RunSnapshot 字典（不把中间事件当作 accepted 版本）。
 
@@ -417,6 +549,8 @@ def run_snapshot(session: Session, run_id: str) -> dict:
         "target_id": run.scene_id or run.chapter_id or "",
         "run_scope": run_scope,
         "request_type": run.request_type or "continue",
+        "plan_revision_id": run.plan_revision_id,
+        "base_scene_revision_id": (run.normalized_input or {}).get("base_scene_revision_id"),
         "status": run.status,
         "run_version": run.run_version,
         "current_scene_id": run.scene_id,
@@ -446,18 +580,14 @@ def _apply_accept_action(
     if body.target == "plan":
         if not body.plan_revision_id:
             raise AppError("PLAN_NOT_ACCEPTED", "plan accept requires plan_revision_id")
-        plan = accept_chapter_plan_revision(
+        accept_chapter_plan_revision(
             session,
             run.chapter_id or "",
             body.plan_revision_id,
-            body.expected_current_plan_revision_id or "",
+            body.expected_current_plan_revision_id,
             body.expected_plan_version or 1,
             ctx,
         )
-        scene_specs = (plan.chapter_contract or {}).get("scenes") or []
-        materialize_chapter_plan(
-            session, run.chapter_id or "", plan.id, scene_specs, ctx
-        )
         return
     if body.target == "scene":
         scene_id = run.scene_id or ""
@@ -517,6 +647,8 @@ def submit_run_decision(
         raise AppError("RUN_STATE_CONFLICT", "paused runs can only be resumed")
     if run.status in ("accepted", "cancelled", "failed", "superseded"):
         raise AppError("RUN_STATE_CONFLICT", "run is in a terminal state")
+    if body.target == "scene":
+        _validate_scene_queue_position(session, run)
     # 决策类型各自定义允许状态：
     # - accept/feedback 只能在匹配的等待状态执行（waiting_feedback 二者均可；
     #   pending_clarification 仅 feedback）；queued/running 不得直接 accept。
@@ -568,6 +700,7 @@ def submit_run_decision(
         fence,
         author_decision=body.decision,
     )
+    child_run: GenerationRun | None = None
     if body.decision == "accept":
         _apply_accept_action(session, run, body, ctx)
         run.status = "accepted"
@@ -576,18 +709,135 @@ def submit_run_decision(
         run.status = "cancelled"
         event_type = _EVENT_CANCELLED
     else:  # feedback
-        run.status = "waiting_feedback"
-        run.decision_target = body.target
         event_type = _EVENT_WAITING_FEEDBACK
-        # 作者反馈只存哈希（fail-open：sink 失败不影响决策事务与命令幂等）。
-        record_author_feedback(
-            sink if sink is not None else get_default_wiring().sink,
-            generation_run_id=run_id,
-            target=body.target,
-            decision="feedback",
-            content=body.text or "",
-        )
-    run.run_version += 1
+        if body.target == "plan":
+            # 计划反馈必须创建同血缘 Planner 子运行。父运行在子运行入队后
+            # 标记 superseded，避免恢复时按时间静默选错运行。
+            feedback = body.feedback or {}
+            lineage = run.parent_plan_revision_id or run.plan_revision_id or run.id
+            chapter_id = run.chapter_id or ""
+            append_plan_discussion_message(
+                session,
+                chapter_id,
+                lineage,
+                role="author",
+                kind="answer" if feedback.get("kind") == "answer" else "feedback",
+                text=body.text or feedback.get("text", ""),
+                source_run_id=run.id,
+                parent_run_id=run.parent_generation_run_id,
+                supersedes_run_id=None,
+            )
+            answers = feedback.get("answers") or []
+            for answer in answers:
+                q = session.get(ChapterPlanQuestion, answer.get("question_id"))
+                if q is not None:
+                    if q.planning_lineage_id != lineage:
+                        raise AppError("PLAN_REVISION_CONFLICT", "question belongs to another planning lineage")
+                    q.status = "answered"
+                append_plan_discussion_message(
+                    session,
+                    chapter_id,
+                    lineage,
+                    role="author",
+                    kind="answer",
+                    text=answer.get("text", ""),
+                    source_run_id=run.id,
+                    parent_run_id=run.id,
+                )
+            for item in feedback.get("proposals") or []:
+                proposal = session.get(ChapterPlanProposal, item.get("proposal_id"))
+                if proposal is None or proposal.planning_lineage_id != lineage:
+                    raise AppError("PLAN_REVISION_CONFLICT", "proposal belongs to another planning lineage")
+                action = item.get("action")
+                proposal.status = {"accept": "accepted", "modify": "modified", "reject": "rejected"}.get(action, proposal.status)
+                append_plan_discussion_message(
+                    session,
+                    chapter_id,
+                    lineage,
+                    role="author",
+                    kind="decision",
+                    text=item.get("field_path", proposal.field_path),
+                    source_run_id=run.id,
+                    parent_run_id=run.id,
+                )
+            child_id = str(uuid.uuid4())
+            child_input = dict(run.normalized_input or {})
+            child_input["author_feedback"] = body.feedback or {"text": body.text or "", "target": "plan"}
+            child_run = GenerationRun(
+                id=child_id,
+                project_id=run.project_id,
+                chapter_id=run.chapter_id,
+                scene_id=None,
+                parent_generation_run_id=run.id,
+                supersedes_run_id=run.id,
+                parent_plan_revision_id=lineage,
+                plan_revision_id=None,
+                request_type=run.request_type or "new_chapter",
+                decision_target="plan",
+                status="queued",
+                run_version=1,
+                write_fencing_token=0,
+                normalized_input=child_input,
+            )
+            session.add(child_run)
+            session.flush()
+            run.status = "superseded"
+            run.run_version += 1
+            PostgresRunEventStore(session).emit(
+                child_id,
+                _EVENT_QUEUED,
+                {"run_scope": "chapter", "request_type": child_run.request_type, "parent_run_id": run.id},
+                fencing_token=0,
+                producer_command_id=manual_command_id,
+            )
+            PostgresRunOutbox(session).enqueue(
+                {
+                    "resource_type": "run",
+                    "resource_id": child_id,
+                    "payload_schema": "run-event.v1",
+                    "payload": {"event_type": _EVENT_QUEUED, "run_id": child_id, "parent_run_id": run.id},
+                    "producer_command_id": manual_command_id,
+                    "generation_run_id": child_id,
+                },
+                fencing_token=0,
+            )
+        else:
+            run.status = "waiting_feedback"
+            run.decision_target = body.target
+            if body.target == "scene" and run.scene_id is not None and run.chapter_id is not None:
+                affected_scene_ids = build_scene_feedback_queue(
+                    session, run.chapter_id, [run.scene_id]
+                )
+                stale_scene_ids = affected_scene_ids[1:]
+                normalized = dict(run.normalized_input or {})
+                normalized["affected_scene_ids"] = affected_scene_ids
+                normalized["stale_scene_ids"] = stale_scene_ids
+                run.normalized_input = normalized
+                for stale_scene_id in stale_scene_ids:
+                    stale_runs = session.execute(
+                        select(GenerationRun)
+                        .where(
+                            GenerationRun.chapter_id == run.chapter_id,
+                            GenerationRun.scene_id == stale_scene_id,
+                            GenerationRun.plan_revision_id == run.plan_revision_id,
+                            GenerationRun.id != run.id,
+                            GenerationRun.status.not_in(("accepted", "cancelled", "failed", "superseded")),
+                        )
+                        .with_for_update()
+                    ).scalars().all()
+                    for stale_run in stale_runs:
+                        stale_run.status = "superseded"
+                        stale_run.run_version += 1
+            # 场景/章节反馈仍写入既有观测 sink；计划正文已写业务讨论表。
+            record_author_feedback(
+                sink if sink is not None else get_default_wiring().sink,
+                generation_run_id=run_id,
+                target=body.target,
+                decision="feedback",
+                content=body.text or "",
+            )
+    if child_run is None:
+        run.run_version += 1
     run.decision_target = body.target
     session.flush()
     decision = append_run_decision(
@@ -620,8 +870,9 @@ def submit_run_decision(
         },
         fencing_token=fence["fencing_token"],
     )
+    response_run_id = child_run.id if child_run is not None else run_id
     return {
-        "run": run_snapshot(session, run_id),
+        "run": run_snapshot(session, response_run_id),
         "decision_id": decision.id,
         "command_id": manual_command_id,
     }
@@ -742,6 +993,7 @@ def _event_envelope(event: Any) -> dict:
 # 供运行 API / outbox 消费者复用的导出（Task 5B 入口）。
 __all__ = [
     "start_generation_run",
+    "persist_planner_output",
     "get_run",
     "run_snapshot",
     "submit_run_decision",

## Added test
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.db.models import ChapterPlanSceneLink, GenerationRun, Scene, SceneRevision
from app.domain.chapters import (
    accept_chapter_plan_revision,
    create_chapter,
    create_chapter_plan_revision,
)
from app.domain.resources import create_project, create_volume
from app.runtime.run_worker import RunWorker


def _chapter(db):
    project = create_project(db, "P-task4", "g", "r", "s", {"actor_id": "a", "idempotency_key": "p-task4"})
    volume = create_volume(db, project.id, "V-task4", "g", "m", "r", {"actor_id": "a", "idempotency_key": "v-task4"})
    return create_chapter(
        db,
        volume.id,
        "C-task4",
        "pov",
        {"text": "继续推进场景", "goal": "finish"},
        {"actor_id": "a", "idempotency_key": "c-task4"},
    )


def test_scene_queue_advances_in_plan_order_after_current_scene_acceptance(db):
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1", "s2"]},
        "fixture",
        {"actor_id": "a", "idempotency_key": "plan-task4"},
    )
    plan.scene_briefs = [
        {"client_key": "s1", "title": "S1", "scene_brief": {}},
        {"client_key": "s2", "title": "S2", "scene_brief": {}},
    ]
    plan.chapter_contract = {"scene_keys": ["s1", "s2"], "scenes": plan.scene_briefs}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        plan.id,
        None,
        1,
        {"actor_id": "a", "idempotency_key": "accept-task4"},
    )
    db.commit()

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = RunWorker(factory, actor_id="worker-task4")
    worker.tick()
    links = db.execute(
        select(ChapterPlanSceneLink)
        .where(ChapterPlanSceneLink.plan_revision_id == plan.id)
        .order_by(ChapterPlanSceneLink.sort_order)
    ).scalars().all()
    first_run = db.execute(
        select(GenerationRun).where(GenerationRun.scene_id == links[0].scene_id)
    ).scalar_one()
    assert first_run.status in {"waiting_feedback", "pending_clarification"}
    assert first_run.plan_revision_id == plan.id
    assert first_run.normalized_input["base_scene_revision_id"] is None

    accepted_revision = SceneRevision(
        scene_id=links[0].scene_id,
        content="accepted scene 1",
        content_hash="a" * 64,
        reason="author acceptance",
        source_ref="task4-test",
        status="accepted",
    )
    db.add(accepted_revision)
    db.flush()
    db.execute(
        update(Scene)
        .where(Scene.id == links[0].scene_id)
        .values(accepted_scene_revision_id=accepted_revision.id)
    )
    first_run.status = "accepted"

    second_accepted_revision = SceneRevision(
        scene_id=links[1].scene_id,
        content="accepted scene 2 baseline",
        content_hash="b" * 64,
        reason="existing baseline",
        source_ref="task4-test",
        status="accepted",
    )
    db.add(second_accepted_revision)
    db.flush()
    db.execute(
        update(Scene)
        .where(Scene.id == links[1].scene_id)
        .values(accepted_scene_revision_id=second_accepted_revision.id)
    )
    db.commit()

    worker.tick()
    second_run = db.execute(
        select(GenerationRun).where(GenerationRun.scene_id == links[1].scene_id)
    ).scalar_one()
    assert second_run.plan_revision_id == plan.id
    assert second_run.normalized_input["base_scene_revision_id"] == second_accepted_revision.id

    # Replaying the accepted-plan outbox remains idempotent.
    worker.tick()
    assert db.execute(
        select(GenerationRun).where(GenerationRun.scene_id == links[1].scene_id)
    ).scalars().all().__len__() == 1

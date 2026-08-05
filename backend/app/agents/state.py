"""LangGraph 运行状态定义模块。

`ChapterRunState` 是单次运行的可恢复 checkpoint 状态：只引用已提交结果与
稳定身份标识，绝不存储未净化的提示词或权威正文副本。所有字段必须能从持久化
运行与清单中重建，以支持断点续跑（resume）。
"""

from __future__ import annotations

from typing import Literal, TypedDict


class ChapterRunState(TypedDict, total=False):
    """单次运行的可恢复 checkpoint 状态。

    只引用已提交结果与稳定身份标识，绝不存储未净化的提示词或权威正文副本。
    这些字段必须能从持久化运行与清单中重建。
    """

    generation_run_id: str
    run_version: int
    project_id: str
    chapter_id: str | None
    scene_id: str | None
    scene_ids: list[str]
    manifest_id: str | None
    pending_node: str | None
    last_durable_node: str | None
    run_status: Literal[
        "pending", "running", "paused", "pending_clarification", "cancelled", "failed", "superseded"
    ]
    base_scene_revision_id: str | None
    base_chapter_revision_id: str | None
    accepted_scene_revision_id: str | None
    accepted_chapter_revision_id: str | None
    plan_revision_id: str | None
    draft_artifact_id: str | None
    clarification_questions: list[str]
    scene_auto_revision_counts: dict[str, int]
    parent_generation_run_id: str | None
    supersedes_run_id: str | None
    parent_plan_revision_id: str | None
    inheritance_map: dict[str, str]
    error_code: str | None
    pause_reason: Literal["clarification", "author_feedback", "technical", "awaiting_decision"] | None
    _pause_action: Literal["accept", "feedback", "cancel", "confirm", "reject", "defer"] | None
    author_feedback: dict | None
    # Task 4C：Canon 分支状态（向后兼容追加，不改变既有字段语义）。
    canon_scope: Literal["chapter", "scene"] | None
    canon_candidates: list[dict]
    candidate_decisions: list[dict]
    # 作者确认命令身份（服务端生成，供正式提交以 author 身份领取 API command fence）。
    manual_command_id: str | None
    decision_idempotency_key: str | None
    expected_run_version: int | None

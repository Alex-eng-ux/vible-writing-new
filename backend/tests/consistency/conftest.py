"""Task 6 一致性测试共享辅助：manifest / 快照 / issue 构造。"""

from __future__ import annotations

from datetime import datetime
from typing import cast

from app.consistency.schemas import ConsistencySnapshot, ReviewIssue
from app.context.models import ContextManifest, ContextManifestEntry


def make_manifest() -> ContextManifest:
    """构造一条含 scene/entity/canon/timeline 来源的 ContextManifest。"""
    return ContextManifest(
        manifest_id="m1",
        generation_run_id="run-1",
        request_fingerprint="fp",
        entries=[
            ContextManifestEntry(
                source_id="scene-1",
                source_type="scene",
                source_revision_id="rev-1",
                resolved_at=datetime(2026, 1, 1),
            ),
            ContextManifestEntry(
                source_id="ent-1",
                source_type="entity",
                source_revision_id="rev-1",
                resolved_at=datetime(2026, 1, 1),
            ),
            ContextManifestEntry(
                source_id="canon-1",
                source_type="canon",
                source_revision_id="rev-1",
                resolved_at=datetime(2026, 1, 1),
            ),
            ContextManifestEntry(
                source_id="tl-1",
                source_type="timeline",
                source_revision_id="rev-1",
                resolved_at=datetime(2026, 1, 1),
            ),
        ],
        entry_handoff_id=None,
        entry_source_chapter_revision_id=None,
        entry_handoff_chain_hash=None,
    )


def make_snapshot(
    *,
    draft_text: str,
    characters: list | None = None,
    locations: list | None = None,
    timeline: list | None = None,
    world_rules: list | None = None,
    terms: list | None = None,
    known_names: list[str] | None = None,
) -> ConsistencySnapshot:
    """构造显式版本快照（默认空事实集合）。"""
    return ConsistencySnapshot(
        scene_id="s1",
        project_id="p1",
        draft_text=draft_text,
        snapshot_revision_ids={"scene": "rev-1", "canon": "rev-1"},
        characters=characters or [],
        locations=locations or [],
        timeline=timeline or [],
        world_rules=world_rules or [],
        terms=terms or [],
        known_names=known_names or [],
    )


def make_issue(**overrides) -> ReviewIssue:
    """构造一条合法 ReviewIssue；overrides 可覆盖任意字段。"""
    issue: dict = {
        "local_key": "lk-1",
        "severity": "low",
        "dimension": "term",
        "text_locator": {"quote": "符祝", "char_start": 0, "char_end": 2},
        "evidence_refs": ["canon-1"],
        "message": "测试问题",
        "suggested_fix": "修复建议",
        "status": "pending",
    }
    issue.update(overrides)
    return cast(ReviewIssue, issue)

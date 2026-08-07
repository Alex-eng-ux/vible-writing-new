"""Task 7C 只读 Canon GET 端点独立 API 单测。

覆盖三个只读端点（此前仅在 Playwright E2E 覆盖，本文件补充独立后端单测）：
- `GET /api/projects/{project_id}/canon`：正式 Story Bible 快照（active 的
  CanonFact / TimelineEvent / PlotThread）；
- `GET /api/scenes/{scene_id}/canon-candidates`：场景级候选列表
  （scope=scene，含 pending 与已决策状态）；
- `GET /api/chapters/{chapter_id}/canon-candidates`：章节级候选列表
  （scope=chapter，scene_id 为空）。

断言覆盖空集、三类正式/候选字段、状态过滤与跨作用域/跨资源隔离。
"""

from __future__ import annotations

from typing import cast

import pytest

from app.db.models import (
    CanonFact,
    Chapter,
    PlotThread,
    TimelineEvent,
)
from app.domain.interfaces import CommandContext
from app.domain.story_bible import upsert_canon_candidates

from .conftest import _create_project, _create_volume
from .test_canon_api import (
    _candidate_payload,
    _make_canon_run,
    _make_canon_run_scene,
    _setup_chapter,
    _setup_scene,
)


@pytest.fixture(autouse=True)
def _cleanup_canon_tables(db):
    """清理本文件经 API/DB 写入共享库的 Canon 记录，保证测试隔离。"""
    yield
    from app.db.models import (
        CanonDecisionRecord,
        CanonFact,
        FactCandidate,
        PlotThread,
        PlotThreadUpdate,
        TimelineEvent,
        TimelineEventCandidate,
    )

    for model in (
        CanonDecisionRecord,
        CanonFact,
        TimelineEvent,
        PlotThread,
        FactCandidate,
        TimelineEventCandidate,
        PlotThreadUpdate,
    ):
        db.query(model).delete()
    db.commit()


def _project_id(db, chapter_id: str) -> str:
    """从章节行反查所属项目 id（复用 test_canon_api 的等价逻辑）。"""
    chapter = db.get(Chapter, chapter_id)
    assert chapter is not None
    from app.db.models import Volume

    volume = db.get(Volume, chapter.volume_id)
    assert volume is not None
    return volume.project_id


def _seed_official_entries(db, project_id: str, chapter_id: str, status: str = "active") -> None:
    """播种三类正式 Canon 条目（CanonFact / TimelineEvent / PlotThread）。"""
    db.add_all([
        CanonFact(
            project_id=project_id,
            fact_text="林默是星门守护者",
            status=status,
        ),
        TimelineEvent(
            project_id=project_id,
            chapter_id=chapter_id,
            event_text="林默在观星台发现星门异动",
            story_time={"value": "第1章", "precision": "exact"},
            entities=["林默"],
            status=status,
        ),
        PlotThread(
            project_id=project_id,
            chapter_id=chapter_id,
            thread_text="星门背后的低语暗示旧神苏醒",
            state="open",
            planned_resolution="第5章",
            status=status,
        ),
    ])
    db.commit()


def _seed_candidates(db, run_id: str, payloads: list[dict]) -> None:
    """经领域函数播种候选（与 test_canon_api 一致的上下文形状）。"""
    # 测试辅助构造最小 agent 命令上下文，沿用 e2e_fixtures 的 cast 约定。
    ctx = cast(CommandContext, {
        "generation_run_id": run_id,
        "actor_id": "a",
        "idempotency_key": f"k-{run_id}",
        "source": "agent",
    })
    upsert_canon_candidates(db, run_id, payloads, ctx)
    db.commit()


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/canon
# ---------------------------------------------------------------------------


def test_project_canon_empty_when_no_entries(client, db) -> None:
    """无正式 Canon 时返回空快照（三列表全空）。"""
    project = _create_project(client)
    resp = client.get(f"/api/projects/{project['id']}/canon")
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == project["id"]
    assert body["facts"] == []
    assert body["timeline_events"] == []
    assert body["plot_threads"] == []


def test_project_canon_returns_active_entries(client, db) -> None:
    """播种三类 active 正式条目后，快照按类型返回完整字段。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, _ = _setup_chapter(db, client, volume["id"])
    _seed_official_entries(db, project["id"], chapter["id"])

    resp = client.get(f"/api/projects/{project['id']}/canon")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["facts"]) == 1
    fact = body["facts"][0]
    assert fact["type"] == "fact"
    assert fact["text"] == "林默是星门守护者"
    assert fact["status"] == "active"

    assert len(body["timeline_events"]) == 1
    te = body["timeline_events"][0]
    assert te["type"] == "timeline_event"
    assert te["text"] == "林默在观星台发现星门异动"
    assert te["story_time"] == {"value": "第1章", "precision": "exact"}
    assert te["entities"] == ["林默"]
    assert te["status"] == "active"

    assert len(body["plot_threads"]) == 1
    pt = body["plot_threads"][0]
    assert pt["type"] == "plot_thread"
    assert pt["text"] == "星门背后的低语暗示旧神苏醒"
    assert pt["state"] == "open"
    assert pt["planned_resolution"] == "第5章"
    assert pt["status"] == "active"


def test_project_canon_excludes_non_active_and_other_project(client, db) -> None:
    """非 active 条目与其他项目的条目都不出现在快照中。"""
    project_a = _create_project(client)
    volume_a = _create_volume(client, project_a["id"])
    chapter_a, _ = _setup_chapter(db, client, volume_a["id"])
    # project_a 播种一条 archived fact（不应返回）。
    db.add(CanonFact(project_id=project_a["id"], fact_text="已归档事实", status="archived"))
    db.commit()

    project_b = _create_project(client)
    volume_b = _create_volume(client, project_b["id"])
    chapter_b, _ = _setup_chapter(db, client, volume_b["id"])
    _seed_official_entries(db, project_b["id"], chapter_b["id"])

    resp = client.get(f"/api/projects/{project_a['id']}/canon")
    assert resp.status_code == 200
    body = resp.json()
    assert body["facts"] == []
    assert body["timeline_events"] == []
    assert body["plot_threads"] == []

    # project_b 只看到自己的条目，不含 project_a 的 archived fact。
    resp_b = client.get(f"/api/projects/{project_b['id']}/canon")
    body_b = resp_b.json()
    assert len(body_b["facts"]) == 1
    assert body_b["facts"][0]["text"] == "林默是星门守护者"


# ---------------------------------------------------------------------------
# GET /api/scenes/{scene_id}/canon-candidates
# ---------------------------------------------------------------------------


def test_scene_candidates_returns_three_types_sorted(client, db) -> None:
    """场景级三类候选按 candidate_type 字典序稳定排序返回，target 元数据正确。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    scene, scene_rev_id = _setup_scene(db, client, chapter["id"])
    project_id = _project_id(db, chapter["id"])
    run = _make_canon_run_scene(client, scene["id"], scene_rev_id, "read-scene-1")

    payloads = [
        _candidate_payload(project_id, chapter["id"], scene_rev_id, scope="scene", scene_id=scene["id"],
                           ctype="plot_thread", local_key="lp", claim="p",
                           content={"claim": "p", "entity_id": None, "paragraph_ref": "p3",
                                    "effective_story_time": {"value": "第3章", "precision": "exact"},
                                    "narrative_knowledge": "objective",
                                    "state": "advanced", "planned_resolution": "第5章"}),
        _candidate_payload(project_id, chapter["id"], scene_rev_id, scope="scene", scene_id=scene["id"],
                           ctype="fact", local_key="lf", claim="f"),
        _candidate_payload(project_id, chapter["id"], scene_rev_id, scope="scene", scene_id=scene["id"],
                           ctype="timeline_event", local_key="le", claim="e",
                           content={"claim": "e", "entity_id": None, "paragraph_ref": "p3",
                                    "effective_story_time": {"value": "第3章", "precision": "exact"},
                                    "narrative_knowledge": "objective"}),
    ]
    _seed_candidates(db, run["run_id"], payloads)

    resp = client.get(f"/api/scenes/{scene['id']}/canon-candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_type"] == "scene"
    assert body["target_id"] == scene["id"]
    # 端点按 candidate_type 字典序排序：fact -> plot_thread -> timeline_event。
    types = [item["candidate_type"] for item in body["items"]]
    assert types == ["fact", "plot_thread", "timeline_event"]
    assert [item["scope"] for item in body["items"]] == ["scene"] * 3
    assert [item["scene_id"] for item in body["items"]] == [scene["id"]] * 3
    assert body["items"][0]["local_key"] == "lf"


def test_scene_candidates_empty_and_isolated(client, db) -> None:
    """无候选返回空；其他场景的候选不混入目标场景列表。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    scene_a, scene_a_rev_id = _setup_scene(db, client, chapter["id"])
    scene_b, _ = _setup_scene(db, client, chapter["id"])
    project_id = _project_id(db, chapter["id"])
    run = _make_canon_run_scene(client, scene_a["id"], scene_a_rev_id, "read-scene-2")

    # 只给 scene_a 播种一条 fact 候选。
    _seed_candidates(
        db, run["run_id"],
        [_candidate_payload(project_id, chapter["id"], scene_a_rev_id, scope="scene",
                            scene_id=scene_a["id"], ctype="fact", local_key="la", claim="a")],
    )

    resp_b = client.get(f"/api/scenes/{scene_b['id']}/canon-candidates")
    assert resp_b.status_code == 200
    assert resp_b.json()["items"] == []

    resp_a = client.get(f"/api/scenes/{scene_a['id']}/canon-candidates")
    items_a = resp_a.json()["items"]
    assert len(items_a) == 1
    assert items_a[0]["local_key"] == "la"
    assert items_a[0]["candidate_type"] == "fact"


# ---------------------------------------------------------------------------
# GET /api/chapters/{chapter_id}/canon-candidates
# ---------------------------------------------------------------------------


def test_chapter_candidates_returns_three_types_and_excludes_scene_level(client, db) -> None:
    """章节级候选返回；场景级候选（scene_id 非空）不混入章节端点。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    scene, scene_rev_id = _setup_scene(db, client, chapter["id"])
    project_id = _project_id(db, chapter["id"])
    chapter_run = _make_canon_run(client, chapter["id"], rev_id, "read-chapter-1")
    scene_run = _make_canon_run_scene(client, scene["id"], scene_rev_id, "read-chapter-scene-1")

    payloads = [
        _candidate_payload(project_id, chapter["id"], rev_id, scope="chapter",
                           ctype="fact", local_key="cf", claim="cf"),
        _candidate_payload(project_id, chapter["id"], rev_id, scope="chapter",
                           ctype="timeline_event", local_key="ce", claim="ce",
                           content={"claim": "ce", "entity_id": None, "paragraph_ref": "p3",
                                    "effective_story_time": {"value": "第2章", "precision": "exact"},
                                    "narrative_knowledge": "objective"}),
        _candidate_payload(project_id, chapter["id"], rev_id, scope="chapter",
                           ctype="plot_thread", local_key="cp", claim="cp",
                           content={"claim": "cp", "entity_id": None, "paragraph_ref": "p3",
                                    "effective_story_time": {"value": "第2章", "precision": "exact"},
                                    "narrative_knowledge": "objective",
                                    "state": "open", "planned_resolution": "第6章"}),
        # 场景级候选：必须在章节端点中不可见。
        _candidate_payload(project_id, chapter["id"], scene_rev_id, scope="scene",
                           scene_id=scene["id"], ctype="fact", local_key="sf", claim="sf"),
    ]
    _seed_candidates(
        db,
        chapter_run["run_id"],
        [payload for payload in payloads if payload["scope"] == "chapter"],
    )
    _seed_candidates(
        db,
        scene_run["run_id"],
        [payload for payload in payloads if payload["scope"] == "scene"],
    )

    resp = client.get(f"/api/chapters/{chapter['id']}/canon-candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_type"] == "chapter"
    assert body["target_id"] == chapter["id"]
    # 端点按 candidate_type 字典序排序：fact -> plot_thread -> timeline_event。
    types = [item["candidate_type"] for item in body["items"]]
    assert types == ["fact", "plot_thread", "timeline_event"]
    # 全部为章节级：scene_id 为空、scope=chapter。
    assert all(item["scene_id"] is None for item in body["items"])
    assert [item["scope"] for item in body["items"]] == ["chapter"] * 3
    assert [item["local_key"] for item in body["items"]] == ["cf", "cp", "ce"]

    # 场景端点仍能看到自己的场景级候选。
    resp_scene = client.get(f"/api/scenes/{scene['id']}/canon-candidates")
    scene_items = resp_scene.json()["items"]
    assert len(scene_items) == 1
    assert scene_items[0]["local_key"] == "sf"


def test_chapter_candidates_empty(client, db) -> None:
    """无章节级候选时返回空列表。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, _ = _setup_chapter(db, client, volume["id"])

    resp = client.get(f"/api/chapters/{chapter['id']}/canon-candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["target_type"] == "chapter"
    assert body["target_id"] == chapter["id"]

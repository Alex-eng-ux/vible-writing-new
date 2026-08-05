from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.change_sets import empty_doc_content, empty_doc_hash
from app.domain.prosemirror import apply_prosemirror_steps

from .conftest import _create_chapter, _create_project, _create_scene, _create_volume


def _setup_scene(client: TestClient) -> str:
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    scene = _create_scene(client, chapter["id"])
    return scene["id"]


def test_empty_scene_first_draft_creates_paired_draft(
    client: TestClient, db: Session
) -> None:
    scene_id = _setup_scene(client)
    resp = client.post(
        f"/api/scenes/{scene_id}/changesets",
        json={
            "base_scene_revision_id": None,
            "operation_format": "prosemirror_step",
            "operations": [{"op": "insert", "value": "x"}],
            "source": "author",
            "base_content_hash": empty_doc_hash(),
        },
        headers={"Idempotency-Key": "cs-1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draft_artifact_id"]
    assert body["change_set_id"]
    assert body["manual_command_id"]
    assert body["base_scene_revision_id"] is None

    from app.db.models import ChangeSet, SceneDraftArtifact

    artifact = db.get(SceneDraftArtifact, body["draft_artifact_id"])
    assert artifact is not None
    # 操作必须真正应用到空文档基线，作者内容不能丢失。
    assert artifact.content == apply_prosemirror_steps(
        empty_doc_content(), [{"op": "insert", "value": "x"}]
    )
    assert "x" in artifact.content
    change_set = db.get(ChangeSet, body["change_set_id"])
    assert change_set is not None
    assert change_set.root_draft_artifact_id == artifact.id
    assert change_set.status == "pending"


def test_commit_root_draft_materializes_root_revision(
    client: TestClient, db: Session
) -> None:
    scene_id = _setup_scene(client)
    resp = client.post(
        f"/api/scenes/{scene_id}/changesets",
        json={
            "base_scene_revision_id": None,
            "operation_format": "prosemirror_step",
            "operations": [{"op": "insert", "value": "首稿正文"}],
            "source": "author",
            "base_content_hash": empty_doc_hash(),
        },
        headers={"Idempotency-Key": "cs-root"},
    )
    body = resp.json()
    commit = client.post(
        f"/api/changesets/{body['change_set_id']}/commit",
        json={"author_decision": "accept"},
        headers={"Idempotency-Key": "commit-root"},
    )
    assert commit.status_code == 200, commit.text
    rev = commit.json()
    assert rev["status"] == "accepted"
    assert rev["parent_revision_id"] is None

    from app.db.models import ChangeSet, SceneRevision

    row = db.get(SceneRevision, rev["id"])
    assert row is not None
    # 根版本必须落盘真实应用后的内容，而不是空文档或占位符。
    assert row.content == apply_prosemirror_steps(
        empty_doc_content(), [{"op": "insert", "value": "首稿正文"}]
    )
    assert "首稿正文" in row.content
    # 提交后 ChangeSet 状态必须更新为 committed。
    cs = db.get(ChangeSet, body["change_set_id"])
    assert cs is not None
    assert cs.status == "committed"


def test_existing_scene_changeset_commits_new_revision(
    client: TestClient, db: Session
) -> None:
    scene_id = _setup_scene(client)
    root = client.post(
        f"/api/scenes/{scene_id}/changesets",
        json={
            "base_scene_revision_id": None,
            "operation_format": "prosemirror_step",
            "operations": [],
            "source": "author",
            "base_content_hash": empty_doc_hash(),
        },
        headers={"Idempotency-Key": "cs-root2"},
    )
    root_id = root.json()["change_set_id"]
    commit = client.post(
        f"/api/changesets/{root_id}/commit",
        json={"author_decision": "accept"},
        headers={"Idempotency-Key": "commit-root2"},
    )
    base_rev_id = commit.json()["id"]

    resp = client.post(
        f"/api/scenes/{scene_id}/changesets",
        json={
            "base_scene_revision_id": base_rev_id,
            "operation_format": "prosemirror_step",
            "operations": [{"op": "insert", "value": "more"}],
            "source": "author",
            "base_content_hash": empty_doc_hash(),
        },
        headers={"Idempotency-Key": "cs-nonroot"},
    )
    assert resp.status_code == 200, resp.text
    nonroot = resp.json()
    assert nonroot["draft_artifact_id"] is None
    assert nonroot["base_scene_revision_id"] == base_rev_id

    commit2 = client.post(
        f"/api/changesets/{nonroot['change_set_id']}/commit",
        json={"author_decision": "accept"},
        headers={"Idempotency-Key": "commit-nonroot"},
    )
    assert commit2.status_code == 200, commit2.text
    assert commit2.json()["parent_revision_id"] == base_rev_id

    from app.db.models import ChangeSet, SceneRevision

    # 非根提交必须把操作应用到基线内容并落盘，绝不写入 "applied"。
    rev2 = db.get(SceneRevision, commit2.json()["id"])
    assert rev2 is not None
    assert rev2.content == apply_prosemirror_steps(
        empty_doc_content(), [{"op": "insert", "value": "more"}]
    )
    assert "more" in rev2.content
    assert rev2.content != "applied"
    cs = db.get(ChangeSet, nonroot["change_set_id"])
    assert cs is not None
    assert cs.status == "committed"


def test_baseline_content_hash_mismatch_returns_stale(client: TestClient) -> None:
    scene_id = _setup_scene(client)
    resp = client.post(
        f"/api/scenes/{scene_id}/changesets",
        json={
            "base_scene_revision_id": None,
            "operation_format": "prosemirror_step",
            "operations": [],
            "source": "author",
            "base_content_hash": "00" * 32,
        },
        headers={"Idempotency-Key": "cs-badbase"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "SCENE_STALE"


def test_rollback_preserves_history_and_sets_parent(
    client: TestClient, db: Session
) -> None:
    scene_id = _setup_scene(client)
    root = client.post(
        f"/api/scenes/{scene_id}/changesets",
        json={
            "base_scene_revision_id": None,
            "operation_format": "prosemirror_step",
            "operations": [],
            "source": "author",
            "base_content_hash": empty_doc_hash(),
        },
        headers={"Idempotency-Key": "cs-rollback"},
    )
    commit = client.post(
        f"/api/changesets/{root.json()['change_set_id']}/commit",
        json={"author_decision": "accept"},
        headers={"Idempotency-Key": "commit-rollback"},
    )
    base_rev_id = commit.json()["id"]

    rollback = client.post(
        f"/api/scenes/{scene_id}/rollback",
        json={"target_revision_id": base_rev_id, "author_decision": "author"},
        headers={"Idempotency-Key": "rollback-1"},
    )
    assert rollback.status_code == 200, rollback.text
    new_rev = rollback.json()
    assert new_rev["parent_revision_id"] == base_rev_id
    assert new_rev["status"] == "staged"

    from app.db.models import SceneRevision

    revisions = db.query(SceneRevision).filter_by(scene_id=scene_id).all()
    assert len(revisions) == 2  # original preserved, rollback added


def test_changeset_idempotency_replays_same_result(client: TestClient) -> None:
    scene_id = _setup_scene(client)
    payload: dict[str, object] = {
        "base_scene_revision_id": None,
        "operation_format": "prosemirror_step",
        "operations": [],
        "source": "author",
        "base_content_hash": empty_doc_hash(),
    }
    first = client.post(
        f"/api/scenes/{scene_id}/changesets",
        json=payload,
        headers={"Idempotency-Key": "cs-dup"},
    )
    second = client.post(
        f"/api/scenes/{scene_id}/changesets",
        json=payload,
        headers={"Idempotency-Key": "cs-dup"},
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()


def test_nonroot_commit_rejects_stale_baseline(client: TestClient) -> None:
    scene_id = _setup_scene(client)
    # 先把场景提到一个 accepted 根版本，作为初始基线 rev1。
    root = client.post(
        f"/api/scenes/{scene_id}/changesets",
        json={
            "base_scene_revision_id": None,
            "operation_format": "prosemirror_step",
            "operations": [],
            "source": "author",
            "base_content_hash": empty_doc_hash(),
        },
        headers={"Idempotency-Key": "cs-stale-baseline"},
    )
    root_commit = client.post(
        f"/api/changesets/{root.json()['change_set_id']}/commit",
        json={"author_decision": "accept"},
        headers={"Idempotency-Key": "commit-stale-baseline"},
    )
    rev1 = root_commit.json()["id"]

    # 以 rev1 为基线创建一个非根 ChangeSet（此时 rev1 仍是当前 accepted）。
    resp = client.post(
        f"/api/scenes/{scene_id}/changesets",
        json={
            "base_scene_revision_id": rev1,
            "operation_format": "prosemirror_step",
            "operations": [{"op": "insert", "value": "more"}],
            "source": "author",
            "base_content_hash": empty_doc_hash(),
        },
        headers={"Idempotency-Key": "cs-stale-nonroot"},
    )
    assert resp.status_code == 200, resp.text
    stale_cs = resp.json()["change_set_id"]

    # 先用另一个基于 rev1 的 ChangeSet 提交，把 accepted 指针推进到 rev2。
    adv = client.post(
        f"/api/scenes/{scene_id}/changesets",
        json={
            "base_scene_revision_id": rev1,
            "operation_format": "prosemirror_step",
            "operations": [{"op": "insert", "value": "advance"}],
            "source": "author",
            "base_content_hash": empty_doc_hash(),
        },
        headers={"Idempotency-Key": "cs-advance"},
    )
    adv_commit = client.post(
        f"/api/changesets/{adv.json()['change_set_id']}/commit",
        json={"author_decision": "accept"},
        headers={"Idempotency-Key": "commit-advance"},
    )
    assert adv_commit.status_code == 200, adv_commit.text

    # 现在 stale_cs 的基线 rev1 已不再是当前 accepted，提交必须被拒绝。
    commit = client.post(
        f"/api/changesets/{stale_cs}/commit",
        json={"author_decision": "accept"},
        headers={"Idempotency-Key": "commit-stale-nonroot"},
    )
    assert commit.status_code == 409, commit.text
    assert commit.json()["code"] == "SCENE_STALE"


def test_duplicate_commit_is_idempotent_and_single_revision(
    client: TestClient, db: Session
) -> None:
    scene_id = _setup_scene(client)
    resp = client.post(
        f"/api/scenes/{scene_id}/changesets",
        json={
            "base_scene_revision_id": None,
            "operation_format": "prosemirror_step",
            "operations": [{"op": "insert", "value": "idem"}],
            "source": "author",
            "base_content_hash": empty_doc_hash(),
        },
        headers={"Idempotency-Key": "cs-dupcommit"},
    )
    change_set_id = resp.json()["change_set_id"]
    payload = {"author_decision": "accept"}
    first = client.post(
        f"/api/changesets/{change_set_id}/commit",
        json=payload,
        headers={"Idempotency-Key": "dup-commit-key"},
    )
    second = client.post(
        f"/api/changesets/{change_set_id}/commit",
        json=payload,
        headers={"Idempotency-Key": "dup-commit-key"},
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()

    from app.db.models import SceneRevision

    revisions = db.query(SceneRevision).filter_by(scene_id=scene_id).all()
    assert len(revisions) == 1  # 重复提交不产生第二条修订
    assert revisions[0].content == apply_prosemirror_steps(
        empty_doc_content(), [{"op": "insert", "value": "idem"}]
    )


def test_commit_idempotency_scope_includes_change_set_id(client: TestClient) -> None:
    # 两个不同场景的 ChangeSet 使用相同幂等键，提交互不干扰（作用域含 change_set_id）。
    scene_a = _setup_scene(client)
    scene_b = _setup_scene(client)
    first = client.post(
        f"/api/scenes/{scene_a}/changesets",
        json={
            "base_scene_revision_id": None,
            "operation_format": "prosemirror_step",
            "operations": [{"op": "insert", "value": "A"}],
            "source": "author",
            "base_content_hash": empty_doc_hash(),
        },
        headers={"Idempotency-Key": "cs-scope-a"},
    )
    second = client.post(
        f"/api/scenes/{scene_b}/changesets",
        json={
            "base_scene_revision_id": None,
            "operation_format": "prosemirror_step",
            "operations": [{"op": "insert", "value": "B"}],
            "source": "author",
            "base_content_hash": empty_doc_hash(),
        },
        headers={"Idempotency-Key": "cs-scope-b"},
    )
    ca = first.json()["change_set_id"]
    cb = second.json()["change_set_id"]

    commit_a = client.post(
        f"/api/changesets/{ca}/commit",
        json={"author_decision": "accept"},
        headers={"Idempotency-Key": "same-key"},
    )
    commit_b = client.post(
        f"/api/changesets/{cb}/commit",
        json={"author_decision": "accept"},
        headers={"Idempotency-Key": "same-key"},
    )
    # 作用域包含 change_set_id，因此不同 ChangeSet 的同键提交互不冲突，
    # 也不会被重放成对方的第一次响应。
    assert commit_a.status_code == 200, commit_a.text
    assert commit_b.status_code == 200, commit_b.text
    assert commit_a.json()["id"] != commit_b.json()["id"]

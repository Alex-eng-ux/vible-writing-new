"""Task 9 V1 验收 CLI：固定 fixture 重置、备份/恢复双哈希、Fake model smoke 与反馈回归。

用法（backend 目录、venv）::

    python -m app.acceptance.cli reset clean --db <url> --fixture <docs/acceptance/v1-fixture.json>
    python -m app.acceptance.cli reset preserve_history --db <url>
    python -m app.acceptance.cli backup --db <url> --out <dir>
    python -m app.acceptance.cli restore --backup <path> --db <url>
    python -m app.acceptance.cli smoke-scene --db <url> --fixture <path>
    python -m app.acceptance.cli feedback-regression --db <url> --fixture <path>

约定（计划书 Task 9）：
- 所有输出只含脱敏 ID/状态/错误码与哈希摘要，不打印密钥、完整正文或 Prompt；
- 断言失败返回非 0 退出码；SKIPPED_PROVIDER_SMOKE 只在 smoke_real_model.ps1 输出；
- Fake model 语义（Agent 均为占位实现），不把结果宣称为真实模型兼容性证据。
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.acceptance.hashes import compute_fixture_hash, snapshot_hashes
from app.api.schemas import RunCreateRequest
from app.db.models import Base, Chapter, GenerationRun, Scene, SceneRevision
from app.domain.chapters import (
    accept_chapter_plan_revision,
    create_chapter,
    create_chapter_plan_revision,
    create_scene,
)
from app.domain.interfaces import CommandContext
from app.domain.manuscript import content_hash
from app.domain.prosemirror import apply_prosemirror_steps, empty_doc_content
from app.domain.resources import create_project, create_volume
from app.runtime.checkpointer import setup_checkpoint_tables
from app.runtime.run_worker import RunWorker
from app.services.generation_runs import start_generation_run

_FIXTURE_CTX = cast(
    CommandContext,
    {"actor_id": "v1-acceptance", "idempotency_key": "v1-fixture"},
)


def _pm_doc(text: str) -> str:
    """把纯文本包装为规范化 ProseMirror 文档（空基线 + insert 操作）。"""
    return apply_prosemirror_steps(empty_doc_content(), [{"op": "insert", "value": text}])


def _seed_scene_accepted(session: Session, scene: Scene, text: str) -> str:
    """播种场景 accepted 版本并更新场景指针（供 continuity 读取基线文本）。"""
    content = _pm_doc(text)
    rev = SceneRevision(
        scene_id=scene.id,
        parent_revision_id=None,
        content=content,
        content_hash=content_hash(content),
        reason="v1 fixture",
        source_ref="v1-acceptance",
        status="accepted",
    )
    session.add(rev)
    session.flush()
    scene.accepted_scene_revision_id = rev.id
    session.flush()
    return rev.id


def _engine(url: str):
    return create_engine(url, pool_pre_ping=True)


def _load_fixture(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "project" in data and "volumes" in data
    return data


def _seed_fixture(session: Session, fixture: dict) -> dict[str, str]:
    """从 v1-fixture.json 创建项目/卷/章/场景，返回 local_key -> 正式 id 映射。"""
    mapping: dict[str, str] = {}
    project = create_project(
        session,
        fixture["project"]["name"],
        fixture["project"]["genre"],
        fixture["project"]["target_reader"],
        fixture["project"]["default_style"],
        _FIXTURE_CTX,
    )
    mapping["project"] = project.id
    vol_by_key = {}
    for vol in fixture["volumes"]:
        v = create_volume(session, project.id, vol["name"], vol["goal"], vol["mainline"], vol["time_range"], _FIXTURE_CTX)
        vol_by_key[vol["local_key"]] = v.id
        mapping[vol["local_key"]] = v.id
    ch_by_key = {}
    for ch in fixture["chapters"]:
        chapter = create_chapter(session, vol_by_key[ch["volume_local_key"]], ch["title"], ch["pov"], ch["chapter_intent"], _FIXTURE_CTX)
        ch_by_key[ch["local_key"]] = chapter.id
        mapping[ch["local_key"]] = chapter.id
    for sc in fixture["scenes"]:
        scene = create_scene(session, ch_by_key[sc["chapter_local_key"]], sc["title"], sc["scene_brief"], _FIXTURE_CTX)
        mapping[sc["local_key"]] = scene.id
    session.flush()
    return mapping


def _reset_clean(session: Session, url: str, fixture: dict) -> dict[str, str]:
    """从空库重建固定 fixture（先 drop/create 领域表 + checkpoint 表）。"""
    engine = _engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()
    setup_checkpoint_tables(url)
    mapping = _seed_fixture(session, fixture)
    session.commit()
    return mapping


def cmd_reset(args: argparse.Namespace) -> int:
    """reset_v1_fixture：clean 从空库重建；preserve_history 只清理临时数据。"""
    engine = _engine(args.db)
    with Session(engine) as session:
        if args.mode == "clean":
            fixture = _load_fixture(args.fixture)
            mapping = _reset_clean(session, args.db, fixture)
            hashes = snapshot_hashes(session)
            print(
                json.dumps(
                    {
                        "mode": "clean",
                        "seeded": len(mapping) - 1,  # 不含 project 键
                        "project_id": mapping["project"],
                        "authority_hash": hashes["authority_hash"],
                        "audit_hash": hashes["audit_hash"],
                        "fixture_hash": compute_fixture_hash(fixture),
                    },
                    ensure_ascii=False,
                )
            )
        else:  # preserve_history：只清理定义为临时的数据（未决运行/事件/租约/未发布 outbox）。
            session.execute(text("DELETE FROM run_events"))
            session.execute(text("DELETE FROM run_leases"))
            session.execute(
                text("DELETE FROM run_outbox_records WHERE delivery_status IN ('pending','failed','publishing')")
            )
            session.execute(text("DELETE FROM generation_runs"))
            session.commit()
            print(json.dumps({"mode": "preserve_history", "temp_cleared": True}, ensure_ascii=False))
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """backup_v1_fixture：导出双哈希与表行数摘要（脱敏，无正文/密钥）。"""
    engine = _engine(args.db)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        hashes = snapshot_hashes(session)
        counts = {}
        for table in (*list(__import__("app.acceptance.hashes", fromlist=["AUTHORITY_TABLES"]).AUTHORITY_TABLES),
                      *list(__import__("app.acceptance.hashes", fromlist=["AUDIT_TABLES"]).AUDIT_TABLES)):
            counts[table] = session.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
        backup = {
            "schema": "v1-backup",
            "created_at": datetime.now(UTC).isoformat(),
            **hashes,
            "table_counts": counts,
        }
        path = out_dir / f"v1-backup-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json"
        path.write_text(json.dumps(backup, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"backup_path": str(path), "authority_hash": hashes["authority_hash"],
                          "audit_hash": hashes["audit_hash"]}, ensure_ascii=False))
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """restore_v1_fixture：校验备份双哈希与当前库一致；不一致返回非 0。"""
    backup = json.loads(Path(args.backup).read_text(encoding="utf-8"))
    engine = _engine(args.db)
    with Session(engine) as session:
        current = snapshot_hashes(session)
    match = current["authority_hash"] == backup.get("authority_hash") and current["audit_hash"] == backup.get("audit_hash")
    print(
        json.dumps(
            {"backup_authority": backup.get("authority_hash"), "db_authority": current["authority_hash"],
             "backup_audit": backup.get("audit_hash"), "db_audit": current["audit_hash"], "match": match},
            ensure_ascii=False,
        )
    )
    return 0 if match else 1


def _create_scene_run(session: Session, scene: Scene, plan_revision_id: str, request_type: str, feedback_text: str) -> dict:
    """经真实 API 服务创建场景运行（幂等键固定，便于重放断言）。"""
    body = RunCreateRequest(
        run_scope="scene",
        request_type=request_type,  # type: ignore[arg-type]
        decision_target="scene",
        plan_revision_id=plan_revision_id,
        base_scene_revision_id=scene.accepted_scene_revision_id,
        author_feedback={"text": feedback_text} if feedback_text else None,
    )
    return start_generation_run(session, "v1-acceptance", scene.id, body, "cmd-v1", "v1-fixture-key")


def _wiring():
    """构建验收用观测装配（显式配置，不依赖进程环境变量）。"""
    from app.config import AppConfig
    from app.observability.wiring import make_wiring

    return make_wiring(
        AppConfig(
            actor_id="v1-acceptance",
            app_env="evaluation",
            deployment_mode="single_user_private",
            api_bind_scope="loopback",
            internal_api_base_url="http://127.0.0.1:8000",
        )
    )


def cmd_smoke_scene(args: argparse.Namespace) -> int:
    """smoke_scene_run：clean 重建 fixture -> 播种 plan -> 创建场景运行 -> Worker 执行并断言。"""
    fixture = _load_fixture(args.fixture)
    engine = _engine(args.db)
    with Session(engine) as session:
        mapping = _reset_clean(session, args.db, fixture)
        # 播种并接受章节 plan（场景运行要求当前 accepted plan）。
        chapter = session.get(Chapter, mapping["c1"])
        assert chapter is not None
        plan = create_chapter_plan_revision(session, chapter.id, None, {"scenes": [], "outline": "v1 fixture"}, "v1 fixture", _FIXTURE_CTX)
        accept_chapter_plan_revision(session, chapter.id, plan.id, cast(str, None), 1, _FIXTURE_CTX)
        session.commit()
        scene = session.get(Scene, mapping["s1"])
        assert scene is not None
        # 播种已接受基线（continuity 校验需要已接受正文），再以 continue 运行。
        _seed_scene_accepted(session, scene, "林默在观星台发现星门异动。")
        session.commit()
        run = _create_scene_run(session, scene, plan.id, "continue", "")
        run_id = run["run_id"]
        session.commit()

    # 进程内 Worker 运行循环执行该运行（Fake model 语义 + 观测自动埋点）。
    factory = sessionmaker(bind=_engine(args.db), expire_on_commit=False)
    wiring = _wiring()
    worker = RunWorker(factory, actor_id="v1-acceptance", observability=wiring)
    processed = worker.tick()
    with Session(_engine(args.db)) as session:
        row = session.get(GenerationRun, run_id)
        events = [e.event_type for e in session.execute(
            text("SELECT event_type FROM run_events WHERE generation_run_id=:rid ORDER BY sequence"), {"rid": run_id}
        ).mappings()]
        # Fake model 语义：Worker 执行一次，运行到达作者交互态（等待反馈或澄清），
        # 事件序列 run_queued -> 结果事件。最小信封下 RevisionAgent 可能要求澄清。
        ok = (
            processed == 1
            and row is not None
            and row.status in ("waiting_feedback", "pending_clarification")
            and len(events) == 2
            and events[0] == "run_queued"
        )
        print(
            json.dumps(
                {"run_id": run_id, "processed": processed, "final_status": row.status if row else None,
                 "events": events, "ok": ok},
                ensure_ascii=False,
            )
        )
    return 0 if ok else 1


def _run_canon_case(session: Session, scene: Scene, accepted_rev_id: str, case: dict, results: list[dict]) -> None:
    """执行一条 canon 反馈回归用例（场景级 Canon 运行 + Worker 执行）。

    成功（到达等待反馈）记 ok；执行失败记 SKIPPED 并保留原因（不伪造通过）。
    """
    from app.api.schemas import CanonRunCreateRequest
    from app.services.canon_runs import start_canon_run

    try:
        body = CanonRunCreateRequest(canon_scope="scene", accepted_scene_revision_id=accepted_rev_id)
        run = start_canon_run(session, "v1-acceptance", "scene", scene.id, body, f"cmd-{case['id']}", case["id"])
        session.commit()
        RunWorker(
            sessionmaker(bind=session.bind, expire_on_commit=False),
            actor_id="v1-acceptance",
            observability=_wiring(),
        ).tick()
        row = session.get(GenerationRun, run["run_id"])
        events = [e.event_type for e in session.execute(
            text("SELECT event_type FROM run_events WHERE generation_run_id=:rid ORDER BY sequence"), {"rid": run["run_id"]}
        ).mappings()]
        ok = row is not None and row.status == "waiting_feedback" and "run_waiting_feedback" in events
        results.append({"id": case["id"], "final_status": row.status if row else None, "events": events, "ok": ok})
        session.execute(text("DELETE FROM run_events WHERE generation_run_id=:rid"), {"rid": run["run_id"]})
        session.execute(text("DELETE FROM run_leases"))
        session.execute(text("DELETE FROM generation_runs WHERE id=:rid"), {"rid": run["run_id"]})
        session.commit()
    except Exception as exc:  # noqa: BLE001 - 记录跳过原因，不伪造通过
        session.rollback()
        results.append({"id": case["id"], "status": "SKIPPED", "reason": f"canon execution failed: {type(exc).__name__}", "ok": False})


def cmd_feedback_regression(args: argparse.Namespace) -> int:
    """author-feedback-10 回归：逐条创建运行并执行，比较 final_status 与事件。"""
    cases = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    assert isinstance(cases, list) and len(cases) == 10
    fixture_path = args.fixture.replace("author-feedback-10.json", "v1-fixture.json")
    fixture = _load_fixture(fixture_path)
    engine = _engine(args.db)
    results: list[dict] = []
    with Session(engine) as session:
        mapping = _reset_clean(session, args.db, fixture)
        chapter = session.get(Chapter, mapping["c1"])
        assert chapter is not None
        plan = create_chapter_plan_revision(session, chapter.id, None, {"scenes": [], "outline": "v1 fixture"}, "v1 fixture", _FIXTURE_CTX)
        accept_chapter_plan_revision(session, chapter.id, plan.id, cast(str, None), 1, _FIXTURE_CTX)
        # 播种已接受基线：revision 需要合法 base_scene_revision_id 才能生成 ChangeSet。
        base_scene = session.get(Scene, mapping["s1"])
        assert base_scene is not None
        accepted_rev_id = _seed_scene_accepted(session, base_scene, "林默在观星台发现星门异动。")
        session.commit()
        for case in cases:
            scene = session.get(Scene, mapping["s1"])
            assert scene is not None
            request_type = case["request"]["request_type"]
            decision_target = case["request"]["decision_target"]
            feedback = (case["request"].get("author_feedback") or {}).get("text", "")
            if decision_target == "canon":
                _run_canon_case(session, scene, accepted_rev_id, case, results)
                continue
            body = RunCreateRequest(
                run_scope="scene",
                request_type=request_type,  # type: ignore[arg-type]
                decision_target="scene",
                plan_revision_id=plan.id,
                base_scene_revision_id=scene.accepted_scene_revision_id,
                author_feedback={"text": feedback} if feedback else None,
            )
            run = start_generation_run(session, "v1-acceptance", scene.id, body, f"cmd-{case['id']}", case["id"])
            session.commit()
            factory = sessionmaker(bind=_engine(args.db), expire_on_commit=False)
            RunWorker(factory, actor_id="v1-acceptance", observability=_wiring()).tick()
            row = session.get(GenerationRun, run["run_id"])
            events = [e.event_type for e in session.execute(
                text("SELECT event_type FROM run_events WHERE generation_run_id=:rid ORDER BY sequence"), {"rid": run["run_id"]}
            ).mappings()]
            expected_status = case["expected"]["final_status"]
            ok = row is not None and row.status == expected_status and "run_waiting_feedback" in events
            results.append({"id": case["id"], "final_status": row.status if row else None, "events": events, "ok": ok})
            session.execute(text("DELETE FROM run_events WHERE generation_run_id=:rid"), {"rid": run["run_id"]})
            session.execute(text("DELETE FROM run_leases"))
            session.execute(text("DELETE FROM generation_runs WHERE id=:rid"), {"rid": run["run_id"]})
            session.commit()
    passed = sum(1 for r in results if r["ok"])
    print(json.dumps({"total": len(results), "passed": passed, "results": results}, ensure_ascii=False))
    return 0 if passed == len(results) else 1


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：解析子命令并执行（失败返回非 0 退出码）。"""
    parser = argparse.ArgumentParser(description="Task 9 V1 acceptance CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_reset = sub.add_parser("reset")
    p_reset.add_argument("mode", choices=["clean", "preserve_history"])
    p_reset.add_argument("--db", required=True)
    p_reset.add_argument("--fixture", default="docs/acceptance/v1-fixture.json")

    p_backup = sub.add_parser("backup")
    p_backup.add_argument("--db", required=True)
    p_backup.add_argument("--out", required=True)

    p_restore = sub.add_parser("restore")
    p_restore.add_argument("--db", required=True)
    p_restore.add_argument("--backup", required=True)

    p_smoke = sub.add_parser("smoke-scene")
    p_smoke.add_argument("--db", required=True)
    p_smoke.add_argument("--fixture", default="docs/acceptance/v1-fixture.json")

    p_fb = sub.add_parser("feedback-regression")
    p_fb.add_argument("--db", required=True)
    p_fb.add_argument("--fixture", default="docs/acceptance/author-feedback-10.json")

    args = parser.parse_args(argv)
    if args.command == "reset":
        return cmd_reset(args)
    if args.command == "backup":
        return cmd_backup(args)
    if args.command == "restore":
        return cmd_restore(args)
    if args.command == "smoke-scene":
        return cmd_smoke_scene(args)
    if args.command == "feedback-regression":
        return cmd_feedback_regression(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

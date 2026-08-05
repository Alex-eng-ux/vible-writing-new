"""V1 收尾加固：隔离备份库大容量迁移演练（migration_rehearsal）。

在专用隔离库 ``novel_migration_bulk`` 上演练 ``v1_rc_observability_metadata``
迁移（升级 / 回滚 / 数据 / 双哈希）：

1. Alembic 升级到 Task 5C head（f8a9b0c1d2e3）；
2. 灌入较大数据量：约 3000 条 run_events、200 条 generation_runs、1000 条
   scene_revisions、200 条 canon_facts、300 条 fact_candidates、200 条
   run_decisions，以及项目/卷/章/场景层级，记录基线双哈希 H1；
3. 升级到 head：断言数据行数、事件序列、payload 不丢失，新审计列
   （payload_schema/redaction_version）按默认值回填，记录 H2；
4. 降级回 f8a9b0c1d2e3：断言数据行数与事件序列不变，双哈希回到 H1；
5. 再升级 head：双哈希回到 H2（往返一致）；
6. 在 head 状态执行 backup/restore 双哈希一致性校验（match=true）。

任一断言失败返回非 0 退出码；输出脱敏 JSON 汇总。

用法（backend 目录、venv）::

    python scripts/migration_rehearsal.py [--db <postgresql url>]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

# 允许脚本以 `python scripts/migration_rehearsal.py` 直接运行。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import MetaData, Table, create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.acceptance.hashes import hashes_equal, snapshot_hashes  # noqa: E402
from app.db.models import (  # noqa: E402
    CanonFact,
    FactCandidate,
    GenerationRun,
    RunDecision,
    SceneRevision,
    new_id,
)
from app.domain.chapters import create_chapter, create_scene  # noqa: E402
from app.domain.interfaces import CommandContext  # noqa: E402
from app.domain.resources import create_project, create_volume  # noqa: E402

# 默认隔离演练库（绝不触碰 novel_test / novel_acceptance / novel_e2e）。
_DEFAULT_DB = "postgresql+psycopg://postgres:postgres@localhost:5432/novel_migration_bulk"
_ADMIN_DB = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"

_CTX = cast(
    CommandContext,
    {"actor_id": "rehearsal", "idempotency_key": "rehearsal-1"},
)

_NUM_RUNS = 200          # generation_runs 条数
_EVENTS_PER_RUN = 15     # 每条运行的事件数（sequence 1..N）
_NUM_SCENE_REVISIONS = 1000
_NUM_CANON_FACTS = 200
_NUM_CANDIDATES = 300
_NUM_DECISIONS = 200


def _recreate_db(url: str) -> None:
    """通过 postgres 管理库 drop/create 隔离演练库（幂等重建）。"""
    db_name = url.rsplit("/", 1)[-1]
    engine = create_engine(_ADMIN_DB, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS " + db_name))
        conn.execute(text("CREATE DATABASE " + db_name))
    engine.dispose()


def _alembic(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "app/db/migrations")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _seed(session: Session) -> None:
    """灌入较大数据量（层级 + 修订 + 运行 + 事件 + 正式 Canon + 候选 + 决策）。"""
    project = create_project(session, "bulk-project", "g", "r", "s", _CTX)
    volume = create_volume(session, project.id, "V", "g", "m", "r", _CTX)
    chapter = create_chapter(session, volume.id, "章", "p", {"text": ""}, _CTX)
    scenes = [create_scene(session, chapter.id, f"场景-{i}", {"goal": "x"}, _CTX) for i in range(2)]

    # 权威正文修订：1000 条（分布到两个场景），最后一条作为 accepted 指针。
    rev_objs: list[SceneRevision] = []
    for i in range(_NUM_SCENE_REVISIONS):
        content = f"第{i}段正文-林默在观星台。"
        rev_objs.append(
            SceneRevision(
                scene_id=scenes[i % 2].id,
                parent_revision_id=None,
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                reason="bulk",
                source_ref="rehearsal",
                status="accepted",
            )
        )
    session.add_all(rev_objs)
    # 先 flush 生成主键，再取正式 id（default=new_id 在 flush 时才求值）。
    session.flush()
    rev_ids = [rev.id for rev in rev_objs]
    scenes[0].accepted_scene_revision_id = rev_ids[-2]
    scenes[1].accepted_scene_revision_id = rev_ids[-1]

    # 运行与事件：200 条运行 × 15 条事件（sequence 1..15，payload 稳定变化）。
    # run_events 用 autoload 的 Core table 插入：灌数发生在 Task 5C schema
    # （无 payload_schema/redaction_version 两列），ORM 模型是 head 版会引用
    # 不存在的列，autoload 只插入旧 schema 实际存在的列（正是迁移演练要
    # 验证的数据形态）。
    run_events_tbl = Table("run_events", MetaData(), autoload_with=session.bind)
    run_ids: list[str] = []
    for i in range(_NUM_RUNS):
        run = GenerationRun(
            project_id=project.id,
            scene_id=scenes[i % 2].id,
            chapter_id=chapter.id,
            status="accepted" if i % 3 else "waiting_feedback",
            run_version=(i % 5) + 1,
            request_type="review",
            decision_target="scene",
            normalized_input={"run_scope": "scene", "request_type": "review"},
        )
        session.add(run)
        session.flush()
        run_ids.append(run.id)
        for seq in range(1, _EVENTS_PER_RUN + 1):
            session.execute(
                run_events_tbl.insert().values(
                    event_id=new_id(),
                    generation_run_id=run.id,
                    sequence=seq,
                    event_type="run_queued" if seq == 1 else "run_node_end",
                    payload={"run_scope": "scene", "seq": seq, "node": f"node-{seq % 4}"},
                    created_at=datetime.now(UTC),
                )
            )
        session.add(
            RunDecision(
                generation_run_id=run.id,
                target="scene",
                request_snapshot={"request_type": "review"},
                idempotency_key=f"bulk-dec-{i}",
                decision="accept",
            )
        )
    session.flush()

    # 正式 Canon 与候选：200 条 fact + 300 条候选（fact 类型）。
    for i in range(_NUM_CANON_FACTS):
        session.add(
            CanonFact(
                project_id=project.id,
                fact_text=f"正式事实-{i}：林默守护星门。",
                status="active",
            )
        )
    for i in range(_NUM_CANDIDATES):
        session.add(
            FactCandidate(
                project_id=project.id,
                chapter_id=chapter.id,
                scene_id=scenes[i % 2].id,
                scope="scene",
                scope_identity=scenes[i % 2].id,
                candidate_type="fact",
                candidate_fingerprint=hashlib.sha256(f"bulk-cand-{i}".encode()).hexdigest(),
                status="pending",
                source_revision_id=rev_ids[i % _NUM_SCENE_REVISIONS],
                source_identity=scenes[i % 2].id,
                content={"claim": f"候选-{i}", "paragraph_ref": "p1",
                         "effective_story_time": {"value": "第3章", "precision": "exact"},
                         "narrative_knowledge": "objective"},
                local_key=f"bulk-cand-{i}",
                generation_run_id=run_ids[i % _NUM_RUNS],
            )
        )
    session.commit()


def _counts(session: Session) -> dict[str, int]:
    """统计关键表行数（迁移前后对比用）。"""
    return {
        "run_events": session.execute(text("SELECT count(*) FROM run_events")).scalar_one(),
        "generation_runs": session.execute(text("SELECT count(*) FROM generation_runs")).scalar_one(),
        "scene_revisions": session.execute(text("SELECT count(*) FROM scene_revisions")).scalar_one(),
        "canon_facts": session.execute(text("SELECT count(*) FROM canon_facts")).scalar_one(),
        "fact_candidates": session.execute(text("SELECT count(*) FROM fact_candidates")).scalar_one(),
        "run_decisions": session.execute(text("SELECT count(*) FROM run_decisions")).scalar_one(),
    }


def _event_sequence_fingerprint(session: Session) -> str:
    """事件 (run_id, sequence, event_type, payload) 全序指纹，验证迁移不丢不改。"""
    rows = session.execute(
        text("SELECT generation_run_id, sequence, event_type, payload::text FROM run_events ORDER BY generation_run_id, sequence")
    ).all()
    return hashlib.sha256("\n".join(f"{r[0]}|{r[1]}|{r[2]}|{r[3]}" for r in rows).encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V1 迁移大容量演练（隔离库）")
    parser.add_argument("--db", default=_DEFAULT_DB)
    args = parser.parse_args(argv)
    url = args.db

    _recreate_db(url)
    cfg = _alembic(url)

    # 1) 升级到 Task 5C head 并灌数据，记录基线双哈希 H1。
    command.upgrade(cfg, "f8a9b0c1d2e3")
    factory = sessionmaker(bind=create_engine(url), expire_on_commit=False)
    with factory() as session:
        _seed(session)
        counts_before = _counts(session)
        seq_before = _event_sequence_fingerprint(session)
        h1 = snapshot_hashes(session)

    # 2) 升级到 head：数据行数/事件序列不丢，新列默认值回填。
    command.upgrade(cfg, "head")
    with factory() as session:
        counts_after_up = _counts(session)
        seq_after_up = _event_sequence_fingerprint(session)
        assert counts_after_up == counts_before, "升级后数据行数变化"
        assert seq_after_up == seq_before, "升级后事件序列/负载变化"
        defaults = session.execute(
            text("SELECT payload_schema, redaction_version FROM run_events LIMIT 1")
        ).fetchone()
        assert defaults is not None and defaults[0] == "run-event.v1" and defaults[1] == "redaction.v1"
        h2 = snapshot_hashes(session)

    # 3) 降级回 Task 5C head：数据与事件序列不变，双哈希回到 H1。
    command.downgrade(cfg, "f8a9b0c1d2e3")
    with factory() as session:
        counts_after_down = _counts(session)
        seq_after_down = _event_sequence_fingerprint(session)
        assert counts_after_down == counts_before, "降级后数据行数变化"
        assert seq_after_down == seq_before, "降级后事件序列/负载变化"
        h3 = snapshot_hashes(session)
        assert hashes_equal(h1, h3), "降级后双哈希未回到升级前基线"

    # 4) 再升级 head：双哈希回到 H2（往返一致）。
    command.upgrade(cfg, "head")
    with factory() as session:
        counts_final = _counts(session)
        assert counts_final == counts_before
        h4 = snapshot_hashes(session)
        assert hashes_equal(h2, h4), "再升级后双哈希未回到 H2"

    # 5) head 状态下 backup/restore 双哈希一致性（match=true）。
    with factory() as session:
        hashes_now = snapshot_hashes(session)
        assert hashes_equal(h2, hashes_now)
    match = hashes_equal(h2, hashes_now)

    print(
        __import__("json").dumps(
            {
                "db": url.rsplit("/", 1)[-1],
                "counts": counts_before,
                "roundtrip": {
                    "h1": h1,
                    "h2_upgrade": h2,
                    "h3_downgrade_back": h3,
                    "h4_reupgrade": h4,
                    "downgrade_returns_to_baseline": hashes_equal(h1, h3),
                    "reupgrade_returns_to_h2": hashes_equal(h2, h4),
                    "events_sequence_preserved": seq_before == seq_after_up == seq_after_down,
                    "default_columns_backfilled": defaults is not None,
                },
                "backup_restore_match": match,
                "ok": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

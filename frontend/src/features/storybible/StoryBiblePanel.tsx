"use client";

/**
 * Task 7C Story Bible 面板：正式 Canon 展示 + 场景/章节 Canon 候选与逐条决策。
 *
 * 语义约束：
 * - 正式 Canon（全局 Story Bible）只由章节级确认物化；场景级确认只更新候选
 *   状态，绝不改变全局 Story Bible（本面板对场景作用域明确提示该差异）；
 * - 决策提交按 Canon 运行分批：一次提交后运行进入终态，因此面板把逐条选择
 *   收集到一批，由"提交决策"统一调用 POST /runs/{run}/canon-decisions；
 * - 所有命令携带 `Idempotency-Key`；同一批决策重试复用同一键（失败不清空）。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  createChapterCanonRun,
  createIdempotencyKey,
  createSceneCanonRun,
  getChapterCanonCandidates,
  getProjectCanon,
  getRun,
  getSceneCanonCandidates,
  submitCanonDecisions,
} from "@/services/api";
import type {
  CanonCandidate,
  CanonDecisionItem,
  CanonEntry,
  CanonSnapshot,
  RunSnapshot,
} from "@/types";

/** 场景/章节级候选目标（后端 canon_scope 决定来源版本与物化语义）。 */
export type CanonTarget = {
  projectId: string;
  scene: { id: string; title: string; acceptedRevisionId: string | null };
  chapter: { id: string; title: string; acceptedRevisionId: string | null };
};

const TYPE_LABEL: Record<string, string> = {
  fact: "事实",
  timeline_event: "时间线事件",
  plot_thread: "情节线",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "待决策",
  accepted: "已确认",
  rejected: "已拒绝",
  deferred: "已暂缓",
  discarded: "已丢弃",
};

const RUN_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  waiting_feedback: "等待决策",
  accepted: "已接受",
  cancelled: "已取消",
  failed: "失败",
};

type Props = {
  target: CanonTarget;
  onStatus: (msg: string) => void;
};

type Decision = "confirm" | "reject" | "defer";

export default function StoryBiblePanel({ target, onStatus }: Props) {
  // 当前候选目标作用域：scene（默认）或 chapter。
  const [scope, setScope] = useState<"scene" | "chapter">("scene");
  const [canon, setCanon] = useState<CanonSnapshot | null>(null);
  const [candidates, setCandidates] = useState<CanonCandidate[]>([]);
  const [run, setRun] = useState<RunSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  // 逐条决策选择（候选 id -> 决策）；由"提交决策"统一成一批调用。
  const [selections, setSelections] = useState<Record<string, Decision>>({});
  // 同一批决策的幂等键：提交失败后重试必须复用同一键（后端按键幂等）。
  const decisionKeyRef = useRef<string | null>(null);

  const scopeLabel = scope === "scene" ? "场景" : "章节";
  const acceptedRevisionId =
    scope === "scene" ? target.scene.acceptedRevisionId : target.chapter.acceptedRevisionId;

  function errorMessage(e: unknown): string {
    if (e instanceof ApiError) return `${e.code}: ${e.message}`;
    return e instanceof Error ? e.message : String(e);
  }

  // 拉取正式 Canon + 当前作用域候选；运行未知时从候选回溯 generation_run_id。
  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const [snap, list] = await Promise.all([
        getProjectCanon(target.projectId),
        scope === "scene"
          ? getSceneCanonCandidates(target.scene.id)
          : getChapterCanonCandidates(target.chapter.id),
      ]);
      setCanon(snap);
      setCandidates(list.items);
      const runId = list.items.find((c) => c.generation_run_id)?.generation_run_id ?? null;
      if (runId) {
        setRun(await getRun(runId));
      }
    } catch (e) {
      onStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }, [target.projectId, target.scene.id, target.chapter.id, scope, onStatus]);

  useEffect(() => {
    // 目标作用域切换后重置本地选择并重新加载。
    setSelections({});
    decisionKeyRef.current = null;
    void refresh();
  }, [refresh, scope]);

  /** 创建当前作用域的 Canon 运行（候选提取由占位 Worker/固定 fixture 完成）。 */
  async function handleStartExtract() {
    if (!acceptedRevisionId || busy) return;
    setBusy(true);
    try {
      const key = createIdempotencyKey("canon_run", scope === "scene" ? target.scene.id : target.chapter.id);
      const created =
        scope === "scene"
          ? await createSceneCanonRun(target.scene.id, {
              canon_scope: "scene",
              accepted_scene_revision_id: acceptedRevisionId,
            }, key)
          : await createChapterCanonRun(target.chapter.id, {
              canon_scope: "chapter",
              accepted_chapter_revision_id: acceptedRevisionId,
            }, key);
      setRun(created);
      onStatus(`已创建${scopeLabel}级 Canon 运行 ${created.run_id.slice(0, 8)}，等待提取结果…`);
      // 候选由外部 fixture 播种；创建后先刷新一次（可能已就绪）。
      await refresh();
    } catch (e) {
      onStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function toggleSelection(candidateId: string, decision: Decision) {
    setSelections((prev) => {
      const next = { ...prev };
      if (next[candidateId] === decision) {
        delete next[candidateId]; // 再次点击取消该条选择。
      } else {
        next[candidateId] = decision;
      }
      return next;
    });
  }

  /** 统一提交本批逐条决策（confirm/reject/defer；提交后运行进入终态）。 */
  async function handleSubmitDecisions() {
    if (!run || run.status !== "waiting_feedback" || busy) return;
    const items: CanonDecisionItem[] = Object.entries(selections)
      .filter(([candidateId]) => candidates.some((c) => c.id === candidateId && c.status === "pending"))
      .map(([candidateId, decision]) => {
        const cand = candidates.find((c) => c.id === candidateId)!;
        return { candidate_id: candidateId, candidate_type: cand.candidate_type, decision, local_key: cand.local_key };
      });
    if (items.length === 0) return;
    setBusy(true);
    // 同一批决策复用同一幂等键：失败重试不产生重复决策。
    decisionKeyRef.current ??= createIdempotencyKey("canon_decide", run.run_id);
    try {
      await submitCanonDecisions(run.run_id, {
        idempotency_key: decisionKeyRef.current,
        expected_run_version: run.run_version,
        canon_scope: run.run_scope,
        decision: "confirm",
        candidate_decisions: items,
      }, decisionKeyRef.current);
      setSelections({});
      decisionKeyRef.current = null;
      onStatus(`已提交 ${items.length} 条 Canon 决策`);
      await refresh();
    } catch (e) {
      onStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const pendingCount = candidates.filter((c) => c.status === "pending").length;
  const selectionCount = Object.keys(selections).length;
  const canSubmit = run?.status === "waiting_feedback" && selectionCount > 0;

  return (
    <div className="story-bible-panel" data-testid="story-bible-panel">
      <h3>Story Bible</h3>

      {/* 目标作用域切换 */}
      <div className="canon-scope-switch">
        <button
          className={scope === "scene" ? "active" : ""}
          data-testid="btn-canon-scope-scene"
          onClick={() => setScope("scene")}
          disabled={busy}
        >
          场景：{target.scene.title}
        </button>
        <button
          className={scope === "chapter" ? "active" : ""}
          data-testid="btn-canon-scope-chapter"
          onClick={() => setScope("chapter")}
          disabled={busy}
        >
          章节：{target.chapter.title}
        </button>
      </div>

      {/* 正式 Canon（全局 Story Bible） */}
      <section className="canon-section" data-testid="canon-official">
        <h4>正式 Canon（全局 Story Bible）</h4>
        {canon && allEntries(canon).length === 0 && <p className="canon-empty">暂无正式条目</p>}
        {canon && allEntries(canon).length > 0 && (
          <ul className="canon-entry-list">
            {allEntries(canon).map((entry) => (
              <li key={`${entry.type}-${entry.id}`} className="canon-entry-item" data-testid={`canon-entry-${entry.type}-${entry.id}`}>
                <span className={`canon-type canon-type-${entry.type}`}>{TYPE_LABEL[entry.type]}</span>
                <span className="canon-entry-text">{entry.text}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Canon 候选 + 逐条决策 */}
      <section className="canon-section" data-testid="canon-candidates">
        <h4>
          Canon 候选（{scopeLabel}级）
          {candidates.length > 0 && <span className="canon-count">共 {candidates.length} 条</span>}
        </h4>
        <div className="canon-candidate-actions">
          <button
            data-testid="btn-canon-start"
            onClick={() => void handleStartExtract()}
            disabled={busy || !acceptedRevisionId}
            title={acceptedRevisionId ? undefined : "尚无 accepted 版本，无法开始提取"}
          >
            {scopeLabel} 提取候选
          </button>
          <button data-testid="btn-canon-refresh" onClick={() => void refresh()} disabled={busy}>
            刷新
          </button>
        </div>
        {!acceptedRevisionId && (
          <p className="canon-hint">当前{scopeLabel}尚无 accepted 版本，先接受场景/章节版本后再提取。</p>
        )}
        {run && (
          <p className="canon-run-status" data-testid="canon-run-status">
            运行 {run.run_id.slice(0, 8)} · {RUN_LABEL[run.status] ?? run.status}
          </p>
        )}
        {/* 作用域语义提示：场景级确认不伪装成全局 Story Bible 更新 */}
        {scope === "scene" && (
          <p className="canon-scope-note">
            场景级确认只更新场景局部 Canon，不会改变全局 Story Bible。
          </p>
        )}
        {candidates.length === 0 && <p className="canon-empty">暂无候选。点击"提取候选"后由固定 fixture 播种。</p>}
        <ul className="canon-candidate-list">
          {candidates.map((c) => (
            <li
              key={c.id}
              className="canon-candidate-item"
              data-testid={`canon-candidate-${c.id}`}
              data-candidate-id={c.id}
              data-candidate-type={c.candidate_type}
              data-candidate-status={c.status}
            >
              <div className="canon-candidate-head">
                <span className={`canon-type canon-type-${c.candidate_type}`}>{TYPE_LABEL[c.candidate_type]}</span>
                <span className={`canon-scope-badge canon-scope-${c.scope}`}>{c.scope === "scene" ? "场景级" : "章节级"}</span>
                <span className={`canon-status canon-status-${c.status}`}>{STATUS_LABEL[c.status]}</span>
                <span className="canon-source" title={c.source_identity}>来源 v{c.source_identity.slice(0, 8)}</span>
              </div>
              <p className="canon-claim">{c.content.claim}</p>
              {c.status === "pending" && run?.status === "waiting_feedback" && (
                <div className="canon-decide-actions">
                  {(["confirm", "reject", "defer"] as Decision[]).map((d) => (
                    <button
                      key={d}
                      className={`canon-decide canon-decide-${d}${selections[c.id] === d ? " active" : ""}`}
                      data-testid={`btn-decide-${d}-${c.id}`}
                      onClick={() => toggleSelection(c.id, d)}
                      disabled={busy}
                    >
                      {d === "confirm" ? "确认" : d === "reject" ? "拒绝" : "暂缓"}
                    </button>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
        {canSubmit && (
          <button className="canon-submit" data-testid="btn-canon-submit" onClick={() => void handleSubmitDecisions()} disabled={busy}>
            提交决策（{selectionCount}/{pendingCount}）
          </button>
        )}
      </section>
    </div>
  );
}

/** 把三类正式条目拼成统一展示列表（保持 fact -> timeline -> thread 顺序）。 */
function allEntries(canon: CanonSnapshot): CanonEntry[] {
  return [...canon.facts, ...canon.timeline_events, ...canon.plot_threads];
}

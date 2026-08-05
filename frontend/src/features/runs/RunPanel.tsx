"use client";

/**
 * Task 7B 运行面板：展示运行状态机、审校问题、澄清问题与 SSE 进度。
 *
 * 状态机约束（按钮与 API 不得混用）：
 * - waiting_feedback：接受（accept）/ 反馈（feedback）/ 取消（cancel）；
 * - pending_clarification：提交澄清（feedback）/ 取消（cancel），不显示接受与恢复；
 * - paused：仅恢复（resume），不显示接受/反馈/取消；
 * - queued/running：仅进度指示，无决策按钮；
 * - accepted/cancelled/failed/superseded：终态，无按钮。
 */

import { useState } from "react";

import type { ReviewIssueItem, RunSnapshot, RunStatus } from "@/types";
import type { SseEvent } from "@/services/sse";

const STATUS_LABEL: Record<RunStatus, string> = {
  queued: "排队中",
  running: "运行中",
  waiting_feedback: "等待反馈",
  pending_clarification: "等待澄清",
  paused: "已暂停",
  accepted: "已接受",
  cancelled: "已取消",
  failed: "失败",
  superseded: "已被取代",
};

type DecisionActions = {
  accept: boolean;
  feedback: boolean;
  cancel: boolean;
  resume: boolean;
};

/** 按运行状态映射可用动作（waiting_feedback/pending_clarification/paused 互斥）。 */
export function decisionActionsFor(status: RunStatus): DecisionActions {
  switch (status) {
    case "waiting_feedback":
      return { accept: true, feedback: true, cancel: true, resume: false };
    case "pending_clarification":
      return { accept: false, feedback: true, cancel: true, resume: false };
    case "paused":
      return { accept: false, feedback: false, cancel: false, resume: true };
    default:
      return { accept: false, feedback: false, cancel: false, resume: false };
  }
}

type Props = {
  run: RunSnapshot;
  issues: ReviewIssueItem[];
  events: SseEvent[];
  busy: boolean;
  onAccept: () => void;
  onFeedback: (text: string) => void;
  onCancel: () => void;
  onResume: () => void;
};

export default function RunPanel({
  run,
  issues,
  events,
  busy,
  onAccept,
  onFeedback,
  onCancel,
  onResume,
}: Props) {
  const [feedbackText, setFeedbackText] = useState("");
  const actions = decisionActionsFor(run.status);
  const questions = run.clarification_questions ?? [];

  function submitFeedback() {
    const text = feedbackText.trim();
    if (!text || busy) return;
    onFeedback(text);
    setFeedbackText("");
  }

  return (
    <div className="run-panel" data-testid="run-panel">
      <h3>
        运行 <span className="run-id" data-testid="run-id" data-full-run-id={run.run_id}>{run.run_id.slice(0, 8)}</span>
        <span className={`run-status run-status-${run.status}`} data-testid="run-status">
          {STATUS_LABEL[run.status]}
        </span>
      </h3>
      <p className="run-meta" data-testid="run-meta">
        {run.request_type} · v{run.run_version}
        {run.pending_node ? ` · 节点 ${run.pending_node}` : ""}
        {run.pause_reason ? ` · 暂停原因 ${run.pause_reason}` : ""}
      </p>

      {/* 审校问题：仅 waiting_feedback 且存在问题时展示 */}
      {run.status === "waiting_feedback" && issues.length > 0 && (
        <div className="review-issues" data-testid="review-issues">
          <h4>审校问题（{issues.length}）</h4>
          <ul>
            {issues.map((issue) => (
              <li key={issue.local_key} className="review-issue-item" data-testid={`issue-${issue.local_key}`}>
                <span className={`issue-severity issue-${issue.severity}`}>{issue.severity}</span>
                <span className="issue-dimension">{issue.dimension}</span>
                <p className="issue-message">{issue.message}</p>
                <p className="issue-fix">{issue.suggested_fix}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 澄清问题：仅 pending_clarification 展示 */}
      {run.status === "pending_clarification" && questions.length > 0 && (
        <div className="clarification-questions" data-testid="clarification-questions">
          <h4>需要澄清</h4>
          <ul>
            {questions.map((q, idx) => (
              <li key={idx} className="clarification-question" data-testid={`question-${idx}`}>
                {q}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 反馈/澄清文本输入：feedback 动作可用时 */}
      {actions.feedback && (
        <div className="feedback-form" data-testid="feedback-form">
          <input
            data-testid="input-run-feedback"
            value={feedbackText}
            onChange={(e) => setFeedbackText(e.target.value)}
            placeholder={run.status === "pending_clarification" ? "输入澄清回答…" : "输入作者反馈…"}
          />
          <button data-testid="btn-run-feedback" onClick={submitFeedback} disabled={busy || !feedbackText.trim()}>
            {run.status === "pending_clarification" ? "提交澄清" : "提交反馈"}
          </button>
        </div>
      )}

      {/* 决策按钮组：按状态机渲染 */}
      <div className="run-actions">
        {actions.accept && (
          <button className="run-accept" data-testid="btn-run-accept" onClick={onAccept} disabled={busy}>
            接受版本
          </button>
        )}
        {actions.cancel && (
          <button className="run-cancel" data-testid="btn-run-cancel" onClick={onCancel} disabled={busy}>
            取消运行
          </button>
        )}
        {actions.resume && (
          <button className="run-resume" data-testid="btn-run-resume" onClick={onResume} disabled={busy}>
            恢复运行
          </button>
        )}
      </div>

      {/* SSE 进度日志 */}
      <div className="run-event-log" data-testid="run-event-log">
        <h4>进度</h4>
        {events.length === 0 && <p className="run-event-empty">暂无事件</p>}
        <ul>
          {events.map((event) => (
            <li key={event.id} className="run-event-item" data-testid={`run-event-${event.id}`}>
              <span className="run-event-type">{event.event}</span>
              <span className="run-event-seq">{event.id.split(":")[1]}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

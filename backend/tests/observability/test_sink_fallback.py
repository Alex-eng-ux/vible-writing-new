"""Task 8 sink 降级测试：LangSmith 关闭/超时/配额时业务继续且不重复执行。

覆盖：
- LocalSink 只保存脱敏元数据（原文不落库），按事件幂等；
- LangSmithSink 未启用（client=None）时跳过且不抛；
- LangSmithSink 网络超时/配额（429）转为本地降级记录，绝不向业务抛出；
- LangSmithSink 幂等：同一事件重试不重复调用外部 API；
- FallbackSink：primary 失败时业务结果不变、业务不重复执行、本地降级记录写入；
- 发送到 LangSmith 的负载必须是脱敏副本（不含原文）。
"""
from __future__ import annotations

import json

from app.observability.events import ErrorEvent, NodeEvent, RunContext, RunEndEvent, RunFeedback
from app.observability.langsmith_sink import LangSmithSink
from app.observability.redaction import REDACTED
from app.observability.sink import FallbackSink, LocalSink


class _StubClient:
    """记录 create_run 调用的 stub；可按需注入失败。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.failure: Exception | None = None

    def create_run(self, name: str, inputs: dict, run_type: str, **kwargs: object) -> None:
        if self.failure is not None:
            raise self.failure
        self.calls.append({"name": name, "inputs": inputs, "run_type": run_type, **kwargs})


def _run_context(gen: str = "run-1") -> RunContext:
    return {
        "generation_run_id": gen,
        "agent_run_id": "agent-1",
        "agent_attempt_key": "attempt-1",
        "project_id": "proj-1",
        "scene_id": "scene-1",
        "chapter_id": "chapter-1",
        "request_type": "continue",
        "environment": "development",
    }


def _node_event() -> NodeEvent:
    return {
        "generation_run_id": "run-1",
        "agent_run_id": "agent-1",
        "node_name": "draft_writer",
        "started_at": "2026-08-04T00:00:00Z",
        "ended_at": "2026-08-04T00:00:01Z",
        "duration_ms": 1000,
        "input_revision_ids": ["rev-a"],
        "output_summary": "草稿完成：林默推开星门",
        "token_usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def _error_event() -> ErrorEvent:
    return {
        "generation_run_id": "run-1",
        "node_name": "draft_writer",
        "error_code": "RUN_STATE_CONFLICT",
        "retryable": False,
        "degraded": False,
        "created_at": "2026-08-04T00:00:00Z",
    }


def _run_end_event(status: str = "accepted") -> RunEndEvent:
    return {
        "generation_run_id": "run-1",
        "status": status,
        "final_decision": "accept",
        "duration_ms": 5000,
        "token_usage": {"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600},
        "degraded_observability": False,
    }


def _feedback() -> RunFeedback:
    return {
        "generation_run_id": "run-1",
        "target": "scene",
        "decision": "feedback",
        "feedback_hash": "sha256-abc",
        "created_at": "2026-08-04T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# LocalSink
# ---------------------------------------------------------------------------


def test_local_sink_records_redacted_metadata() -> None:
    """本地 sink 记录运行/节点/错误/终态/反馈；原文不落库，ID 保留。"""
    sink = LocalSink()
    sink.on_run_start(_run_context())
    sink.on_node_end(_node_event())
    sink.on_error(_error_event())
    sink.on_run_end(_run_end_event())
    sink.record_feedback(_feedback())

    kinds = [r["kind"] for r in sink.records]
    assert kinds == ["run_start", "node_end", "error", "run_end", "feedback"]

    node = next(r for r in sink.records if r["kind"] == "node_end")
    assert node["node_name"] == "draft_writer"  # 枚举/名称保留
    assert node["input_revision_ids"] == ["rev-a"]  # ID 列表保留
    assert node["token_usage"]["total_tokens"] == 120
    # 输出摘要（正文片段）被脱敏。
    assert node["output_summary"] == REDACTED

    end = next(r for r in sink.records if r["kind"] == "run_end")
    assert end["status"] == "accepted"
    assert end["final_decision"] == "accept"
    assert end["duration_ms"] == 5000

    feedback = next(r for r in sink.records if r["kind"] == "feedback")
    assert feedback["feedback_hash"] == "sha256-abc"
    assert "redaction_version" in feedback


def test_local_sink_no_raw_content_stored() -> None:
    """本地 sink 记录中不得出现任何原文敏感片段。"""
    sink = LocalSink()
    sink.on_run_start(_run_context())
    sink.on_node_end(_node_event())
    blob = json.dumps(sink.records, ensure_ascii=False)
    assert "林默推开星门" not in blob  # output_summary 原文不得出现
    assert "林默" not in blob


def test_local_sink_idempotent_per_run() -> None:
    """同一事件重复记录只保留一条（幂等）。"""
    sink = LocalSink()
    sink.on_run_end(_run_end_event())
    sink.on_run_end(_run_end_event())
    assert len([r for r in sink.records if r["kind"] == "run_end"]) == 1


# ---------------------------------------------------------------------------
# LangSmithSink
# ---------------------------------------------------------------------------


def test_langsmith_skipped_when_disabled() -> None:
    """LangSmith 未启用（无 client）时跳过发送且不抛、不降级。"""
    sink = LangSmithSink(client=None)
    sink.on_run_start(_run_context())
    sink.on_run_end(_run_end_event())
    assert sink.send_attempts == 0
    assert sink.degraded == []


def test_langsmith_sends_redacted_only() -> None:
    """发送到 LangSmith 的负载必须是脱敏副本（原文不离开进程边界）。"""
    stub = _StubClient()
    sink = LangSmithSink(client=stub, project="novel-e2e")
    sink.on_node_end(_node_event())
    assert len(stub.calls) == 1
    payload = stub.calls[0]["inputs"]
    assert payload["output_summary"] == REDACTED
    assert payload["input_revision_ids"] == ["rev-a"]
    assert stub.calls[0]["project_name"] == "novel-e2e"
    assert stub.calls[0]["run_type"] == "chain"


def test_langsmith_timeout_degrades_without_raise() -> None:
    """网络超时：捕获并记录 timeout_or_network 降级，不向业务抛出。"""
    stub = _StubClient()
    stub.failure = TimeoutError("connection timed out")
    sink = LangSmithSink(client=stub)
    sink.on_run_start(_run_context())  # 不抛
    assert len(sink.degraded) == 1
    assert sink.degraded[0]["reason"] == "timeout_or_network"


def test_langsmith_quota_degrades_without_raise() -> None:
    """API 配额错误（429）：捕获并记录 quota 降级，不向业务抛出。"""
    stub = _StubClient()
    stub.failure = RuntimeError("429 Too Many Requests: rate limit exceeded")
    sink = LangSmithSink(client=stub)
    sink.on_run_end(_run_end_event())  # 不抛
    assert len(sink.degraded) == 1
    assert sink.degraded[0]["reason"] == "quota"


def test_langsmith_service_error_degrades() -> None:
    """其他服务错误：记录 service_error 降级。"""
    stub = _StubClient()
    stub.failure = RuntimeError("server exploded")
    sink = LangSmithSink(client=stub)
    sink.record_feedback(_feedback())  # 不抛
    assert sink.degraded[0]["reason"] == "service_error"


def test_langsmith_idempotent_no_duplicate_calls() -> None:
    """同一事件重试不重复调用外部 API（幂等）。"""
    stub = _StubClient()
    sink = LangSmithSink(client=stub)
    sink.on_run_end(_run_end_event())
    sink.on_run_end(_run_end_event())
    assert len(stub.calls) == 1


# ---------------------------------------------------------------------------
# FallbackSink
# ---------------------------------------------------------------------------


def test_fallback_primary_failure_does_not_break_business() -> None:
    """primary（LangSmith）失败：业务结果不变、不抛异常、本地降级记录写入。"""
    stub = _StubClient()
    stub.failure = RuntimeError("429 quota exceeded")
    local = LocalSink()
    primary = LangSmithSink(client=stub, on_degraded=local.record_degradation)
    fallback = FallbackSink(primary=primary, local=local)

    # 业务命令 + 观测包裹：观测失败不改变业务结果，也不触发业务重试。
    executions = 0

    def business_action() -> dict:
        nonlocal executions
        executions += 1
        result = {"status": "accepted", "run_version": 2}
        fallback.on_run_end(_run_end_event())  # primary 失败但被吞掉
        return result

    result = business_action()
    assert result == {"status": "accepted", "run_version": 2}  # 业务结果不变
    assert executions == 1  # 业务不因观测失败重复执行
    assert len(local.records) == 1  # 本地仍记录脱敏 run_end
    assert any(d.get("reason") == "quota" for d in local.degraded)  # 降级已记录


def test_fallback_primary_disabled_routes_to_local() -> None:
    """LangSmith 关闭：FallbackSink 只走本地，不抛异常。"""
    local = LocalSink()
    fallback = FallbackSink(primary=None, local=local)
    fallback.on_run_start(_run_context())
    fallback.on_node_end(_node_event())
    fallback.on_run_end(_run_end_event())
    assert [r["kind"] for r in local.records] == ["run_start", "node_end", "run_end"]
    assert local.degraded == []


def test_fallback_idempotent_across_sinks() -> None:
    """同一事件重复路由只发送一次（primary 与 local 都幂等）。"""
    stub = _StubClient()
    local = LocalSink()
    primary = LangSmithSink(client=stub)
    fallback = FallbackSink(primary=primary, local=local)
    fallback.on_run_end(_run_end_event())
    fallback.on_run_end(_run_end_event())
    assert len(stub.calls) == 1
    assert len([r for r in local.records if r["kind"] == "run_end"]) == 1

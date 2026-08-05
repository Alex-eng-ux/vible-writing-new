"""Task 8 脱敏测试：敏感字段脱敏、白名单保留、内容采集开关与 fail-closed。

覆盖：
- Prompt/正文/候选/用户输入等字符串默认脱敏为 ``[redacted]``；
- 结构化 ID、状态、枚举、时间戳等白名单键保留；
- 列表元素继承父键（input_revision_ids 保留，entities 脱敏）；
- 确定性：同一输入恒得同一输出；
- ``capture_content`` 与环境门控（``content_capture_allowed``）；
- ``find_leaks`` 泄漏检测（评测用）。
"""
from __future__ import annotations

from app.observability.redaction import (
    REDACTED,
    REDACTION_VERSION,
    collect_sensitive_values,
    content_capture_allowed,
    find_leaks,
    redact_payload,
)


def test_content_keys_redacted_by_default() -> None:
    """prompt/正文/候选/用户输入等内容键默认脱敏。"""
    payload = {
        "generation_run_id": "run-1",
        "prompt": "请续写：林默推开星门",
        "content": "林默是星门守护者",
        "draft_text": "林默在观星台发现星门异动",
        "author_feedback": "请调整语气",
        "candidate": {"claim": "星门背后的低语暗示旧神苏醒"},
        "user_input": "目标角色是林默",
    }
    out = redact_payload(payload)
    assert out["generation_run_id"] == "run-1"  # ID 保留
    assert out["prompt"] == REDACTED
    assert out["content"] == REDACTED
    assert out["draft_text"] == REDACTED
    assert out["author_feedback"] == REDACTED
    assert out["user_input"] == REDACTED
    assert out["candidate"]["claim"] == REDACTED  # 嵌套内容也脱敏


def test_whitelist_keys_preserved() -> None:
    """结构化 ID、状态、枚举、时间戳与数值保留。"""
    payload = {
        "generation_run_id": "run-9",
        "project_id": "proj-1",
        "agent_run_id": "agent-7",
        "node_name": "draft_writer",
        "status": "accepted",
        "run_scope": "scene",
        "request_type": "continue",
        "error_code": "RUN_STATE_CONFLICT",
        "duration_ms": 1234,
        "created_at": "2026-08-04T00:00:00Z",
        "input_revision_ids": ["rev-a", "rev-b"],
        "token_usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        "entities": ["林默", "星门"],
    }
    out = redact_payload(payload)
    assert out["status"] == "accepted"
    assert out["node_name"] == "draft_writer"
    assert out["run_scope"] == "scene"
    assert out["request_type"] == "continue"
    assert out["error_code"] == "RUN_STATE_CONFLICT"
    assert out["duration_ms"] == 1234
    assert out["created_at"] == "2026-08-04T00:00:00Z"
    # 列表元素继承父键：ID 列表保留，entities（故事内容）脱敏。
    assert out["input_revision_ids"] == ["rev-a", "rev-b"]
    assert out["entities"] == [REDACTED, REDACTED]
    assert out["token_usage"] == {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}


def test_redaction_is_deterministic() -> None:
    """确定性：同一输入恒得同一输出。"""
    payload = {"run": {"id": "r-1", "text": "林默是星门守护者"}, "items": ["a", "b"]}
    assert redact_payload(payload) == redact_payload(payload)


def test_capture_content_preserves_strings() -> None:
    """capture_content=True（经环境门控）时保留字符串内容。"""
    payload = {"generation_run_id": "r-1", "prompt": "请续写：林默推开星门"}
    out = redact_payload(payload, capture_content=True)
    assert out["prompt"] == "请续写：林默推开星门"


def test_content_capture_allowed_gating() -> None:
    """内容采集只允许 development/evaluation；production 即使开关为真也拒绝。"""
    assert content_capture_allowed("development", True) is True
    assert content_capture_allowed("evaluation", True) is True
    assert content_capture_allowed("production", True) is False
    assert content_capture_allowed("development", False) is False


def test_redact_keeps_original_untouched() -> None:
    """redact 返回新结构，不修改原负载。"""
    payload = {"id": "r-1", "prompt": "原文"}
    snapshot = dict(payload)
    redact_payload(payload)
    assert payload == snapshot


def test_collect_sensitive_values_and_find_leaks() -> None:
    """泄漏检测：脱敏输出不含任何原文敏感值。"""
    payload = {
        "generation_run_id": "run-1",
        "prompt": "林默推开星门",
        "text": "林默是星门守护者",
        "status": "pending",
    }
    sensitive = collect_sensitive_values(payload)
    assert "林默推开星门" in sensitive
    assert "林默是星门守护者" in sensitive
    assert "run-1" not in sensitive  # ID 不在敏感值内
    assert "pending" not in sensitive

    out = redact_payload(payload)
    assert find_leaks(payload, out) == []  # 无泄漏

    # 对照组：未脱敏输出必被检出泄漏。
    assert find_leaks(payload, payload) != []


def test_redaction_version_constant() -> None:
    """脱敏规则版本固定为 redaction.v1。"""
    assert REDACTION_VERSION == "redaction.v1"
    assert REDACTED == "[redacted]"

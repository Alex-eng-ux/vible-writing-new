"""观测脱敏：对 Prompt、正文、候选和用户输入执行确定性脱敏。

规则（default-deny）：
- 所有 sink 只接收脱敏副本；除 ``KEEP_STRING_KEYS`` 白名单（结构化 ID、状态、
  枚举、时间戳、数值等）外，任何字符串值都被替换为 ``"[redacted]"``，同一输入
  恒得同一输出（确定性）；
- ``capture_content=True`` 时保留字符串内容（仅允许在 ``development``/
  ``evaluation`` 环境显式开启，生产环境即使开关为真也由 ``config.py`` 拒绝启动）；
- 脱敏失败必须 fail-closed：调用方不得把原文发送到 sink（见 ``redact_payload``
  与 sink 的降级记录）。

本模块不修改供路由、状态或领域服务使用的规范化业务结果；结构化 ID、定位与
引用关系保持不变（ID/版本/状态键在白名单内）。
"""
from __future__ import annotations

import copy
from typing import Any

# 脱敏规则版本：sink 记录与评测报告必须携带该版本，消费方按版本解析。
REDACTION_VERSION = "redaction.v1"
REDACTED = "[redacted]"

# 允许采集完整内容的环境（与 config.CONTENT_CAPTURE_ENVS 保持一致）。
CONTENT_CAPTURE_ENVS = ("development", "evaluation")

# 保留的字符串键白名单：结构化 ID、状态/枚举、时间戳、URL、来源引用等。
# 列表元素继承父键，因此 ``input_revision_ids`` 等 ID 列表的字符串元素也会保留。
KEEP_STRING_KEYS = frozenset(
    {
        # 结构化 ID
        "id", "generation_run_id", "agent_run_id", "agent_attempt_key", "project_id",
        "volume_id", "chapter_id", "scene_id", "revision_id", "parent_revision_id",
        "input_revision_ids", "source_revision_id", "source_draft_artifact_id",
        "source_change_set_id", "change_set_id", "draft_artifact_id", "run_id",
        "thread_id", "decision_id", "command_id", "request_id", "manual_command_id",
        "local_key", "candidate_id", "target_id", "parent_generation_run_id",
        "supersedes_run_id", "plan_revision_id", "parent_plan_revision_id",
        "entry_handoff_id", "source_ref", "source_identity", "scope_identity",
        "feedback_hash", "content_hash", "base_content_hash", "request_fingerprint",
        "trace_id", "fingerprint", "candidate_fingerprint", "authority_hash",
        "audit_hash", "entity_id", "base_scene_revision_id",
        "accepted_scene_revision_id", "accepted_chapter_revision_id",
        # 状态 / 枚举 / 决策
        "status", "state", "run_status", "run_scope", "request_type",
        "decision_target", "decision", "final_decision", "author_decision",
        "cancel_scope", "canon_scope", "scope", "candidate_type", "event_type",
        "payload_schema", "redaction_version", "environment", "app_env",
        "deployment_mode", "api_bind_scope", "target", "target_type", "dimension",
        "severity", "reason", "error_code", "retryable", "degraded",
        "degraded_observability", "lease_owner", "write_owner_kind",
        "write_owner_id", "current_node", "pending_node", "pause_reason", "node",
        "node_name", "metric", "formula", "threshold", "baseline_version",
        "source", "plan_status", "operation_format", "agent_type", "attempt_no",
        "narrative_knowledge", "precision", "signal", "name", "genre",
        # 时间 / URL
        "created_at", "updated_at", "started_at", "ended_at", "trace_url", "url",
        "llm_base_url", "internal_api_base_url",
        # 版本 / 计数（字符串形态兜底；数值类型不依赖白名单）
        "version", "run_version", "plan_version", "sequence", "duration_ms",
        "count", "sample_count", "denominator", "total", "calls", "call",
    }
)


def content_capture_allowed(app_env: str, capture_content: bool) -> bool:
    """返回是否允许采集完整内容（capture_content 且环境为 dev/eval）。

    参数：app_env 为运行环境；capture_content 为配置开关。
    返回：True 时 sink 可保留原文；False 时一律脱敏。
    """
    return bool(capture_content and app_env in CONTENT_CAPTURE_ENVS)


def redact(value: Any, *, capture_content: bool = False) -> Any:
    """对任意负载做确定性脱敏（默认 deny 内容），返回脱敏副本。

    参数：value 为待脱敏负载（dict/list/标量）；capture_content 为 True 时保留
    字符串内容（环境开关由调用方按 ``content_capture_allowed`` 决定）。
    返回：脱敏后的新结构，原对象不被修改。
    """
    return _redact(value, capture_content=capture_content, key=None)


def _redact(value: Any, *, capture_content: bool, key: str | None) -> Any:
    """递归脱敏实现。

    - dict：逐键递归，键名作为子值/子结构的判定键；
    - list：元素继承父键（ID 列表的元素因此保留）；
    - 字符串：键在白名单则保留，否则替换为 ``REDACTED``；
    - 数值/布尔/None：原样保留。
    capture_content=True 时整体保留字符串内容（信任环境开关）。
    """
    if capture_content:
        return copy.deepcopy(value)
    if isinstance(value, dict):
        return {k: _redact(v, capture_content=False, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, capture_content=False, key=key) for v in value]
    if isinstance(value, str):
        if key in KEEP_STRING_KEYS:
            return value
        return REDACTED
    return value


def redact_payload(payload: dict, *, capture_content: bool = False) -> dict:
    """返回负载的脱敏副本；脱敏失败时抛错（fail-closed），调用方不得发送原文。

    参数：payload 为原始负载；capture_content 为内容采集开关。
    返回：脱敏后的字典副本。
    失败条件：无法序列化/处理的负载直接抛异常，由 sink 记录降级而非发送原文。
    """
    return _redact(payload, capture_content=capture_content, key=None)


def collect_sensitive_values(payload: dict) -> list[str]:
    """收集负载中按默认 deny 规则应被脱敏的字符串值（用于泄漏检测）。

    参数：payload 为原始负载。
    返回：所有落在白名单之外的字符串值列表（列表元素继承父键判定）。
    """
    values: list[str] = []

    def walk(node: Any, key: str | None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif isinstance(node, str) and key not in KEEP_STRING_KEYS:
            values.append(node)

    walk(payload, None)
    return values


def find_leaks(payload: dict, redacted: dict) -> list[str]:
    """返回出现在脱敏输出中的原文敏感值（空列表表示无泄漏）。

    参数：payload 为原始负载；redacted 为脱敏输出。
    返回：泄漏到脱敏输出中的原始敏感字符串列表。
    """
    import json

    redacted_json = json.dumps(redacted, sort_keys=True, ensure_ascii=False)
    return [v for v in collect_sensitive_values(payload) if v and v in redacted_json]


__all__ = [
    "REDACTION_VERSION",
    "REDACTED",
    "CONTENT_CAPTURE_ENVS",
    "KEEP_STRING_KEYS",
    "content_capture_allowed",
    "redact",
    "redact_payload",
    "collect_sensitive_values",
    "find_leaks",
]

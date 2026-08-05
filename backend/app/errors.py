from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class ErrorEnvelope(TypedDict):
    """错误信封：统一对外返回的错误结构。

    字段：code 为稳定错误码；message 为人类可读信息；retryable 表示是否可重试；
    run_id 为关联的生成运行 id（可为空）；request_id 为请求 id；details 为可选
    附加详情。
    """

    code: str
    message: str
    retryable: bool
    run_id: str | None
    request_id: str
    details: dict | None


@dataclass(frozen=True)
class ErrorSpec:
    """错误码的静态规格：固定 HTTP 状态码、可重试语义与默认消息。

    字段：code 为稳定错误码；http_status 为固定 HTTP 状态码；retryable 表示
    是否可重试；default_message 为未提供自定义消息时使用的默认消息。
    """

    code: str
    http_status: int
    retryable: bool
    default_message: str


# 稳定错误码注册表。下方每个错误码都有固定的 HTTP 状态码、可重试语义与消息
# 形态。路由与 agent 不得自行发明同义错误码，必须引用本注册表。
ERROR_SPECS: tuple[ErrorSpec, ...] = (
    ErrorSpec("RUN_STATE_CONFLICT", 409, False, "run state does not allow the requested operation"),
    ErrorSpec("RUN_LEASE_LOST", 409, True, "run lease was lost; retry with a fresh lease"),
    ErrorSpec("IDEMPOTENCY_KEY_REUSE", 409, False, "idempotency key reused with a different request"),
    ErrorSpec("IDEMPOTENCY_IN_PROGRESS", 409, True, "request with this idempotency key is still in progress"),
    ErrorSpec("ACTOR_OVERRIDE_FORBIDDEN", 403, False, "client cannot override the configured actor identity"),
    ErrorSpec("CHECKPOINT_EXPIRED", 410, False, "checkpoint has expired and can no longer be resumed"),
    ErrorSpec("COMMAND_CONTEXT_MISMATCH", 409, False, "command context does not match the request"),
    ErrorSpec("CONTEXT_BUDGET_EXCEEDED", 429, True, "context budget exceeded"),
    ErrorSpec("CONTEXT_MANIFEST_MISMATCH", 409, False, "context manifest does not match the expected sources"),
    ErrorSpec("CONTEXT_SOURCE_UNAVAILABLE", 404, False, "a referenced context source is unavailable"),
    ErrorSpec("RESOURCE_REFERENCED", 409, False, "resource is still referenced and cannot be deleted"),
    ErrorSpec("PLAN_REVISION_CONFLICT", 409, False, "plan revision conflict; expected revision does not match"),
    ErrorSpec("PLAN_NOT_ACCEPTED", 409, False, "plan has not been accepted by the author"),
    ErrorSpec("CANON_NOT_ENABLED", 503, False, "canon endpoint is not enabled in this stage"),
    ErrorSpec("CANON_USE_DEDICATED_ENDPOINT", 400, False, "use the dedicated canon endpoint for this operation"),
    ErrorSpec("CHAPTER_HANDOFF_CONFLICT", 409, False, "chapter handoff conflict; inherited version is invalid"),
    ErrorSpec("CHAPTER_OUT_OF_SYNC", 409, False, "chapter is out of sync and cannot be updated"),
    ErrorSpec("SCENE_NOT_ACCEPTED", 409, False, "scene has not been accepted by the author"),
    ErrorSpec("SCENE_ACTIVE_RUN", 409, False, "scene already has an active run"),
    ErrorSpec("SCENE_STALE", 409, False, "scene baseline is stale; refresh and retry"),
    ErrorSpec("SCENE_PLAN_MISMATCH", 409, False, "scene does not match the accepted plan"),
    ErrorSpec("SCENE_STATE_INCOMPATIBLE", 409, False, "scene state does not allow the requested operation"),
    # 真实模型 Provider 错误码（Task 9 真实 Agent 接线追加，additive）：与
    # smoke_real_model.ps1 的映射表保持一致，供 Agent 节点/运行错误信封使用。
    ErrorSpec("LLM_AUTH_ERROR", 401, False, "model provider rejected the API key"),
    ErrorSpec("LLM_INVALID_REQUEST", 400, False, "model provider rejected the request"),
    ErrorSpec("LLM_ENDPOINT_NOT_FOUND", 404, False, "model provider endpoint not found"),
    ErrorSpec("LLM_RATE_LIMITED", 429, True, "model provider rate limited"),
    ErrorSpec("LLM_SERVER_ERROR", 502, True, "model provider server error"),
    ErrorSpec("LLM_UNAVAILABLE", 503, True, "model provider unavailable or timed out"),
    ErrorSpec("LLM_RESPONSE_INVALID", 422, True, "model response failed structured output validation"),
    ErrorSpec("LLM_UNKNOWN_ERROR", 502, True, "unknown model provider error"),
    # 全局异常处理使用的通用兜底错误码。
    ErrorSpec("VALIDATION_ERROR", 422, False, "request validation failed"),
    ErrorSpec("INTERNAL_ERROR", 500, False, "internal server error"),
)


REGISTRY: dict[str, ErrorSpec] = {spec.code: spec for spec in ERROR_SPECS}


def get_error_spec(code: str) -> ErrorSpec:
    """按错误码查注册表；未知错误码回退到 INTERNAL_ERROR。

    参数：code 为要查询的错误码。
    返回：对应 ErrorSpec；若 code 未注册，返回 INTERNAL_ERROR 的规格。
    """
    try:
        return REGISTRY[code]
    except KeyError:
        return REGISTRY["INTERNAL_ERROR"]


class AppError(Exception):
    """携带稳定错误码以及可选的运行/请求上下文。

    从错误码注册表解析出 HTTP 状态码与可重试语义，作为异常的四散字段暴露。
    """

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        """构造 AppError。

        参数：code 为稳定错误码（必须存在于注册表，否则回退 INTERNAL_ERROR）；
        message 为可选自定义消息，缺省用注册表中的默认消息；run_id/request_id
        为可选的关联上下文；details 为可选的附加详情。
        副作用：初始化 code、http_status、retryable、message、run_id、
        request_id、details 等字段，并调用基类异常初始化。
        """
        spec = get_error_spec(code)
        self.code = code
        self.http_status = spec.http_status
        self.retryable = spec.retryable
        self.message = message or spec.default_message
        self.run_id = run_id
        self.request_id = request_id
        self.details = details
        super().__init__(self.message)


def build_envelope(
    code: str,
    message: str | None = None,
    *,
    run_id: str | None = None,
    request_id: str = "",
    details: dict | None = None,
) -> ErrorEnvelope:
    """按错误码构造统一的错误信封字典。

    参数：code 为稳定错误码；message 为可选自定义消息，缺省用注册表默认消息；
    run_id/request_id 为可选的关联上下文；details 为可选的附加详情。
    返回：符合 ErrorEnvelope 结构的字典，包含 code、message、retryable、
    run_id、request_id、details。
    说明：未知错误码会回退到 INTERNAL_ERROR；retryable 取自注册表对错误码的
    固定语义。
    """
    spec = get_error_spec(code)
    return ErrorEnvelope(
        code=spec.code,
        message=message or spec.default_message,
        retryable=spec.retryable,
        run_id=run_id,
        request_id=request_id,
        details=details,
    )

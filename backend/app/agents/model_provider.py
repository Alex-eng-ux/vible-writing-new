"""统一模型 Provider：Agent 通过端口调用真实模型（Task 9 真实 Agent 接线）。

设计（additive，不修改既有 Agent/图/领域契约）：
- ``ModelProvider`` 是 Agent 依赖的端口协议：输入 Prompt，返回解析后的 JSON
  对象（dict）；失败统一抛 ``AppError(LLM_*)``，由运行层映射为失败终态
  （run_failed + last_error_code），绝不产生版本/候选/Canon 数据；
- ``DeepSeekModelProvider`` 是 OpenAI 兼容 chat/completions 的真实实现：使用
  httpx 出站请求；错误映射（401/403 -> LLM_AUTH_ERROR、400 ->
  LLM_INVALID_REQUEST、404 -> LLM_ENDPOINT_NOT_FOUND、429 ->
  LLM_RATE_LIMITED、5xx -> LLM_SERVER_ERROR、超时/连接失败 ->
  LLM_UNAVAILABLE、非 JSON/结构化输出失败 -> LLM_RESPONSE_INVALID）与
  ``scripts/smoke_real_model.ps1`` 的映射表保持一致；真实调用经
  ``traced_call``（kind=llm）自动上报，sink 失败 fail-open；
  对可重试错误（限流/不可用/服务端/未知）以有限次数 + 指数退避自动重试，
  认证错误与结构化响应错误不重试；重试只发生在模型出站调用层，不产生重复
  业务写入；
- API Key 只经构造函数从配置/环境变量传入，绝不写入日志、输出或任何交付物；
- 默认测试保留 Fake model，真实 provider 只在显式装配（base_url/api_key/
  model_name 齐全）时生效；http_client 可注入用于测试（httpx.MockTransport）。
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from typing import Protocol

import httpx

from app.errors import AppError
from app.observability.wiring import ObservabilityWiring

# 仅对传输/限流类可重试错误自动重试。认证错误（LLM_AUTH_ERROR）、请求被拒
# （LLM_INVALID_REQUEST/LLM_ENDPOINT_NOT_FOUND）与结构化响应错误
# （LLM_RESPONSE_INVALID）不得重试：前两者重试无意义且可能放大风险，后者表明
# 模型输出不合 schema，重试大概率重演同一缺陷。注意 LLM_RESPONSE_INVALID 在
# 错误注册表标记 retryable=True，但重试决策以本白名单为准，显式排除它。
_RETRYABLE_CODES = frozenset(
    {
        "LLM_RATE_LIMITED",
        "LLM_UNAVAILABLE",
        "LLM_SERVER_ERROR",
        "LLM_UNKNOWN_ERROR",
    }
)


class ModelProvider(Protocol):
    """Agent 依赖的模型端口：返回解析后的 JSON 对象，失败抛 AppError(LLM_*)。

    `system_prompt` 为必填：每个 Agent 必须传入与其输出 schema 对齐的独立
    系统提示词（见 ``app.agents.prompts``），不提供默认提示词，避免未对齐。
    """

    def invoke_structured(
        self,
        *,
        prompt: str,
        generation_run_id: str,
        agent_run_id: str,
        node_name: str,
        system_prompt: str,
    ) -> dict:
        """调用模型并返回解析后的 JSON 对象。

        参数：prompt 为用户侧写作指令；generation_run_id / agent_run_id /
        node_name 为所属运行、Agent 与节点（供观测 traced_call 关联层级）；
        system_prompt 为与该 Agent 输出 schema 对齐的系统提示词（必填）。
        返回：模型输出的 JSON 对象（dict，未做业务 schema 校验）。
        失败条件：任何 provider 错误抛 ``AppError``，错误码取自 LLM_* 注册表。
        """
        ...


def _normalize_endpoint(base_url: str) -> str:
    """把 base_url 规范化为 chat/completions 端点（与 smoke 脚本一致）。"""
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions") or "/v1/chat/completions" in base:
        return base
    return f"{base}/chat/completions"


def _map_status(status: int) -> tuple[str, bool]:
    """把真实 provider 的 HTTP 状态映射为稳定错误码与可重试语义。"""
    if status in (401, 403):
        return "LLM_AUTH_ERROR", False
    if status == 400:
        return "LLM_INVALID_REQUEST", False
    if status == 404:
        return "LLM_ENDPOINT_NOT_FOUND", False
    if status == 429:
        return "LLM_RATE_LIMITED", True
    if status >= 500:
        return "LLM_SERVER_ERROR", True
    return "LLM_UNKNOWN_ERROR", True


class DeepSeekModelProvider:
    """OpenAI 兼容 chat/completions 的真实模型 Provider（DeepSeek 等）。

    API Key 只经构造函数注入（通常来自配置，而配置只从环境变量读取），
    绝不写入日志/输出。真实调用经 ``traced_call``（kind=llm）自动上报；
    sink 失败 fail-open，不影响业务。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout: float = 60.0,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        wiring: ObservabilityWiring | None = None,
        http_client: httpx.Client | None = None,
        max_retries: int = 3,
        retry_backoff: float = 0.5,
        retry_jitter: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """构造真实 Provider。

        参数：base_url 为 provider 根地址；api_key 为 API Key（只从配置/环境
        变量传入）；model_name 为模型名；timeout 为请求超时秒数；max_tokens /
        temperature 为采样参数；wiring 为观测装配（提供 traced_call）；http_client
        为可注入的 httpx 客户端（测试用 MockTransport），缺省自建。
        max_tokens 默认 4096：真实模型（如 deepseek-v4-flash）在 512 下会以
        finish_reason=length 截断长 JSON 导致无法解析；实测场景写作约 0.4k 字符、
        Canon 候选抽取约 2.1-2.6k 字符，4096 足以承载最长输出 + schema。
        max_retries 为首次调用失败后的**额外**重试次数（有限次，默认 3）；仅对
        可重试错误（限流/不可用/服务端/未知）重试，认证与结构化响应错误不重试。
        retry_backoff 为退避基数秒数，retry_jitter 为最大随机抖动秒数（指数退避
        base * 2**attempt + jitter）；sleep 为可注入的休眠函数（测试用，缺省
        time.sleep）。
        """
        if not base_url or not api_key or not model_name:
            raise ValueError("base_url/api_key/model_name must be non-empty")
        self._endpoint = _normalize_endpoint(base_url)
        self._api_key = api_key
        self._model_name = model_name
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._wiring = wiring
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers={"Content-Type": "application/json"},
        )
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._retry_jitter = retry_jitter
        self._sleep = sleep

    def close(self) -> None:
        """释放自建的 httpx 客户端（注入的客户端由调用方管理）。"""
        if self._owns_client:
            self._client.close()

    def invoke_structured(
        self,
        *,
        prompt: str,
        generation_run_id: str,
        agent_run_id: str,
        node_name: str,
        system_prompt: str,
    ) -> dict:
        """调用模型并返回解析后的 JSON 对象（真实出站请求）。

        参数：prompt 为用户侧写作指令；generation_run_id / agent_run_id /
        node_name 供观测 traced_call 关联层级；system_prompt 为与该 Agent
        输出 schema 对齐的系统提示词（必填，无默认值）。
        返回：模型输出的 JSON 对象（dict）。
        失败条件：超时/连接失败抛 ``LLM_UNAVAILABLE``；HTTP 错误按状态映射为
        LLM_AUTH_ERROR / LLM_INVALID_REQUEST / LLM_ENDPOINT_NOT_FOUND /
        LLM_RATE_LIMITED / LLM_SERVER_ERROR / LLM_UNKNOWN_ERROR；响应非 JSON
        或缺少可解析内容抛 ``LLM_RESPONSE_INVALID``。所有错误消息只含状态与
        脱敏摘要，绝不包含 API Key。
        """
        payload = {
            "model": self._model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self._wiring is not None:
            decorated = self._wiring.traced_call(
                name=f"{self._model_name}.chat",
                kind="llm",
                generation_run_id=generation_run_id,
                agent_run_id=agent_run_id,
                node_name=node_name,
            )
            post = decorated(self._post)
        else:
            post = self._post
        # 重试只发生在模型出站调用层：每次尝试发起一次独立 HTTP 请求，绝不
        # 重新执行 Agent 业务逻辑，因此不会产生重复的版本/候选/Canon/事件写入。
        return self._invoke_with_retry(post, payload)

    def _invoke_with_retry(self, post: Callable[[dict], dict], payload: dict) -> dict:
        """在可重试错误上以有限次数 + 指数退避重试模型出站调用。

        参数：post 为已包装（含观测）的 `_post` 调用；payload 为请求体。
        返回：一次成功的模型 JSON 对象。
        失败条件：不可重试错误立即上抛；可重试错误在耗尽 `max_retries` 次额外
        尝试后上抛最后一次错误；重试期间不产生任何业务写入。
        """
        attempt = 0
        while True:
            try:
                return post(payload)
            except AppError as exc:
                if exc.code not in _RETRYABLE_CODES or attempt >= self._max_retries:
                    raise
                delay = self._retry_delay(attempt)
                self._sleep(delay)
                attempt += 1

    def _retry_delay(self, attempt: int) -> float:
        """计算第 `attempt` 次重试的退避延迟：指数退避 + 随机抖动。"""
        base = self._retry_backoff * (2**attempt)
        return base + random.uniform(0, self._retry_jitter)

    def _post(self, payload: dict) -> dict:
        """执行真实出站 POST 并解析 JSON 对象（错误映射 + 脱敏消息）。

        API Key 只写入本次请求头（内存中构造），绝不进入日志/输出。
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        try:
            resp = self._client.post(self._endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AppError(
                "LLM_UNAVAILABLE",
                f"model provider timed out (endpoint={self._endpoint})",
                details={"endpoint": self._endpoint},
            ) from exc
        except httpx.HTTPError as exc:
            # 连接失败 / DNS / 其他传输层错误：不可达视为不可用，可重试。
            raise AppError(
                "LLM_UNAVAILABLE",
                f"model provider connection failed (endpoint={self._endpoint})",
                details={"endpoint": self._endpoint},
            ) from exc
        if resp.status_code >= 400:
            code, retryable = _map_status(resp.status_code)
            # 错误消息只含状态码，不包含响应体原文（避免泄漏敏感信息）。
            raise AppError(
                code,
                f"model provider returned http_status={resp.status_code}",
                details={"http_status": resp.status_code, "retryable": retryable},
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise AppError(
                "LLM_RESPONSE_INVALID",
                "model provider returned non-JSON response",
                details={"http_status": resp.status_code},
            ) from exc
        try:
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AppError(
                "LLM_RESPONSE_INVALID",
                "model response is not a parseable JSON object",
                details={"http_status": resp.status_code},
            ) from exc


__all__ = ["ModelProvider", "DeepSeekModelProvider"]

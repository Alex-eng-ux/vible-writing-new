"""DeepSeekModelProvider 单元测试（httpx.MockTransport，默认不访问网络）。

覆盖：真实请求与响应解析、端点/请求头/请求体断言、错误映射（401/429/5xx/
超时/非 JSON/内容缺失）、traced_call（kind=llm）自动埋点与错误事件。
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.agents.model_provider import DeepSeekModelProvider
from app.errors import AppError
from app.observability.sink import LocalSink
from app.observability.wiring import ObservabilityWiring


def _provider(
    transport: httpx.MockTransport,
    *,
    wiring: ObservabilityWiring | None = None,
    max_retries: int = 0,
    sleep=None,
) -> DeepSeekModelProvider:
    client = httpx.Client(transport=transport, timeout=httpx.Timeout(5))
    kwargs: dict = {"wiring": wiring, "http_client": client, "max_retries": max_retries}
    if sleep is not None:
        kwargs["sleep"] = sleep
    return DeepSeekModelProvider(
        base_url="https://api.deepseek.com",
        api_key="sk-test-key",
        model_name="deepseek-v4-flash",
        **kwargs,
    )


def _json_response(status: int = 200, content: str | None = None) -> httpx.Response:
    """构造 OpenAI 兼容信封：content 为模型返回的 JSON 字符串。"""
    if content is None:
        content = json.dumps({"status": "ready", "mode": "draft", "content": "草稿"})
    return httpx.Response(status, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


def test_provider_sends_structured_request_and_returns_parsed_json() -> None:
    """真实请求：端点/鉴权头/模型名/response_format 正确；返回解析后的 JSON 对象。"""
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return _json_response(200)

    provider = _provider(httpx.MockTransport(handler))
    result = provider.invoke_structured(
        prompt="写一个雨夜咖啡馆场景",
        generation_run_id="r1",
        agent_run_id="a1",
        node_name="writing",
        system_prompt="You are a novel-writing agent",
    )
    assert result["status"] == "ready"
    assert result["content"] == "草稿"
    assert seen["url"] == "https://api.deepseek.com/chat/completions"
    assert seen["auth"] == "Bearer sk-test-key"
    assert seen["body"]["model"] == "deepseek-v4-flash"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert any(m["role"] == "system" for m in seen["body"]["messages"])


def test_provider_maps_401_to_auth_error() -> None:
    """401 -> LLM_AUTH_ERROR（retryable=False）。"""
    provider = _provider(httpx.MockTransport(lambda req: httpx.Response(401, json={"error": {"message": "auth"}})))
    with pytest.raises(AppError) as exc:
        provider.invoke_structured(prompt="x", generation_run_id="r", agent_run_id="a", node_name="writing", system_prompt="sys")
    assert exc.value.code == "LLM_AUTH_ERROR"
    assert exc.value.retryable is False


def test_provider_maps_429_to_rate_limited() -> None:
    """429 -> LLM_RATE_LIMITED（retryable=True）。"""
    provider = _provider(httpx.MockTransport(lambda req: httpx.Response(429, json={"error": {"message": "limit"}})))
    with pytest.raises(AppError) as exc:
        provider.invoke_structured(prompt="x", generation_run_id="r", agent_run_id="a", node_name="writing", system_prompt="sys")
    assert exc.value.code == "LLM_RATE_LIMITED"
    assert exc.value.retryable is True


def test_provider_maps_500_to_server_error() -> None:
    """5xx -> LLM_SERVER_ERROR（retryable=True）。"""
    provider = _provider(httpx.MockTransport(lambda req: httpx.Response(503, json={})))
    with pytest.raises(AppError) as exc:
        provider.invoke_structured(prompt="x", generation_run_id="r", agent_run_id="a", node_name="writing", system_prompt="sys")
    assert exc.value.code == "LLM_SERVER_ERROR"
    assert exc.value.retryable is True


def test_provider_maps_timeout_to_unavailable() -> None:
    """超时/连接失败 -> LLM_UNAVAILABLE（retryable=True）。"""
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=req)

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(AppError) as exc:
        provider.invoke_structured(prompt="x", generation_run_id="r", agent_run_id="a", node_name="writing", system_prompt="sys")
    assert exc.value.code == "LLM_UNAVAILABLE"
    assert exc.value.retryable is True


def test_provider_maps_non_json_body_to_response_invalid() -> None:
    """响应体不是 JSON -> LLM_RESPONSE_INVALID。"""
    provider = _provider(httpx.MockTransport(lambda req: httpx.Response(200, text="<html>not json</html>")))
    with pytest.raises(AppError) as exc:
        provider.invoke_structured(prompt="x", generation_run_id="r", agent_run_id="a", node_name="writing", system_prompt="sys")
    assert exc.value.code == "LLM_RESPONSE_INVALID"


def test_provider_maps_non_json_content_to_response_invalid() -> None:
    """choices[0].message.content 不是 JSON -> LLM_RESPONSE_INVALID。"""
    provider = _provider(httpx.MockTransport(lambda req: _json_response(200, content="not a json object")))
    with pytest.raises(AppError) as exc:
        provider.invoke_structured(prompt="x", generation_run_id="r", agent_run_id="a", node_name="writing", system_prompt="sys")
    assert exc.value.code == "LLM_RESPONSE_INVALID"


def test_provider_maps_missing_choices_to_response_invalid() -> None:
    """响应缺少 choices -> LLM_RESPONSE_INVALID。"""
    provider = _provider(httpx.MockTransport(lambda req: httpx.Response(200, json={"id": "x"})))
    with pytest.raises(AppError) as exc:
        provider.invoke_structured(prompt="x", generation_run_id="r", agent_run_id="a", node_name="writing", system_prompt="sys")
    assert exc.value.code == "LLM_RESPONSE_INVALID"


def test_provider_traced_call_records_llm_event_and_error() -> None:
    """真实调用经 traced_call：成功上报 llm 事件；失败上报 error 事件并原样重抛。"""
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")

    def handler(req: httpx.Request) -> httpx.Response:
        return _json_response(200)

    provider = _provider(httpx.MockTransport(handler), wiring=wiring)
    provider.invoke_structured(prompt="x", generation_run_id="r1", agent_run_id="a1", node_name="writing", system_prompt="sys")
    assert wiring.local is not None
    kinds = [r["kind"] for r in wiring.local.records]
    assert "node_end" in kinds
    node_names = [r.get("node_name") for r in wiring.local.records]
    assert "writing:llm:deepseek-v4-flash.chat" in node_names

    # 失败路径：error 事件被上报且异常原样上抛（LLM_UNAVAILABLE）。
    def fail_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=req)

    wiring2 = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    provider2 = _provider(httpx.MockTransport(fail_handler), wiring=wiring2)
    with pytest.raises(AppError) as exc:
        provider2.invoke_structured(prompt="x", generation_run_id="r2", agent_run_id="a2", node_name="writing", system_prompt="sys")
    assert exc.value.code == "LLM_UNAVAILABLE"
    assert wiring2.local is not None
    error_codes = [r.get("error_code") for r in wiring2.local.records if r.get("kind") == "error"]
    assert error_codes == ["LLM_UNAVAILABLE"]


def test_provider_retries_rate_limited_then_succeeds() -> None:
    """可重试错误（429）后成功：重试一次即成功，出站调用两次、休眠一次。"""
    attempts = {"n": 0}
    sleeps: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "limit"}})
        return _json_response(200)

    provider = _provider(httpx.MockTransport(handler), max_retries=3, sleep=sleeps.append)
    result = provider.invoke_structured(prompt="x", generation_run_id="r", agent_run_id="a", node_name="writing", system_prompt="sys")
    assert result["status"] == "ready"
    assert attempts["n"] == 2
    assert len(sleeps) == 1
    # 退避为正：指数退避 + 抖动，首次延迟约 retry_backoff(=0.5) 数量级。
    assert sleeps[0] >= 0.5


def test_provider_retry_exhausts_max_retries_then_raises() -> None:
    """可重试错误持续存在：耗尽 max_retries 次额外尝试后上抛，总尝试 = max_retries+1。"""
    attempts = {"n": 0}
    sleeps: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, json={})

    provider = _provider(httpx.MockTransport(handler), max_retries=3, sleep=sleeps.append)
    with pytest.raises(AppError) as exc:
        provider.invoke_structured(prompt="x", generation_run_id="r", agent_run_id="a", node_name="writing", system_prompt="sys")
    assert exc.value.code == "LLM_SERVER_ERROR"
    assert attempts["n"] == 4  # 首次 + 3 次重试
    assert len(sleeps) == 3  # 每次重试前休眠一次


def test_provider_does_not_retry_auth_error() -> None:
    """认证错误（401）不重试：单次尝试即上抛。"""
    attempts = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(401, json={"error": {"message": "auth"}})

    provider = _provider(httpx.MockTransport(handler), max_retries=3, sleep=lambda d: None)
    with pytest.raises(AppError) as exc:
        provider.invoke_structured(prompt="x", generation_run_id="r", agent_run_id="a", node_name="writing", system_prompt="sys")
    assert exc.value.code == "LLM_AUTH_ERROR"
    assert attempts["n"] == 1


def test_provider_does_not_retry_response_invalid() -> None:
    """结构化响应错误（LLM_RESPONSE_INVALID）不重试：单次尝试即上抛。"""
    attempts = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return _json_response(200, content="not a json object")

    provider = _provider(httpx.MockTransport(handler), max_retries=3, sleep=lambda d: None)
    with pytest.raises(AppError) as exc:
        provider.invoke_structured(prompt="x", generation_run_id="r", agent_run_id="a", node_name="writing", system_prompt="sys")
    assert exc.value.code == "LLM_RESPONSE_INVALID"
    assert attempts["n"] == 1


def test_provider_retry_backoff_is_exponential() -> None:
    """退避随重试次数指数增长（重试 0/1/2 的延迟约为 0.5/1.0/2.0 数量级）。"""
    attempts = {"n": 0}
    sleeps: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(429, json={})

    provider = _provider(httpx.MockTransport(handler), max_retries=3, sleep=sleeps.append)
    with pytest.raises(AppError):
        provider.invoke_structured(prompt="x", generation_run_id="r", agent_run_id="a", node_name="writing", system_prompt="sys")
    assert len(sleeps) == 3
    # 抖动上限 0.1，故延迟落在 [base, base+0.1] 内。
    assert 0.5 <= sleeps[0] < 0.6
    assert 1.0 <= sleeps[1] < 1.1
    assert 2.0 <= sleeps[2] < 2.1

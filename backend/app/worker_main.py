from __future__ import annotations

import logging

from .agents.model_provider import DeepSeekModelProvider, ModelProvider
from .config import get_config
from .db.session import get_session_factory
from .observability.wiring import make_wiring
from .runtime.run_worker import RunWorker

logger = logging.getLogger("novel-studio.worker")


def _build_provider(cfg, wiring) -> ModelProvider | None:
    """配置齐全（base_url/api_key/model_name 非空）时构建真实模型 Provider。

    返回：真实 Provider；任一项缺失时返回 None（保持 Fake model 语义，不访问
    网络）。API Key 只来自配置（即环境变量），绝不写入日志或任何输出。
    """
    if cfg.llm_base_url and cfg.llm_api_key and cfg.model_name:
        return DeepSeekModelProvider(
            base_url=cfg.llm_base_url,
            api_key=cfg.llm_api_key,
            model_name=cfg.model_name,
            wiring=wiring,
        )
    return None


def main() -> None:
    """Worker 进程入口：构建生产观测装配并进入运行循环。

    先调用 get_config() 完成 fail-closed 配置校验（任何非法配置都会在启动时
    抛错，进程不会在不安全状态下运行），再构建生产观测装配 make_wiring()
    （LangSmith 未启用/无 Key 时只用本地 sink），随后以 RunWorker 运行循环
    领取并执行 ``queued`` 运行：经 RunExecutor + observability 自动埋点
    （run_start/node_end/run_end/error）。真实模型接线：配置齐全时场景图
    WritingAgent 经 DeepSeekModelProvider 调用真实模型（traced_call 自动埋点），
    未配置时保持 Fake model 语义（不调用外部 provider）。
    观测失败 fail-open，不影响业务与执行次数。
    """
    # Fail-closed 配置校验在 get_config() 内完成；观测装配同样只读配置，不依赖
    # 外部服务（无真实 LangSmith API Key 时自动降级本地）。
    cfg = get_config()
    wiring = make_wiring(cfg)
    provider = _build_provider(cfg, wiring)
    worker = RunWorker(
        get_session_factory(),
        actor_id=cfg.actor_id,
        observability=wiring,
        provider=provider,
    )
    logger.info(
        "worker_ready",
        extra={
            "app_env": cfg.app_env,
            "deployment_mode": cfg.deployment_mode,
            "api_bind_scope": cfg.api_bind_scope,
            "actor_id": cfg.actor_id,
            "observability_sink": type(wiring.sink).__name__,
            "real_model_provider": provider is not None,
        },
    )
    worker.run_forever(interval=1.0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

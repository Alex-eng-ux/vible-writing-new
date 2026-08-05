from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ENV_VALUES = ("development", "evaluation", "production")
DEPLOYMENT_MODE_VALUES = ("single_user_private",)
API_BIND_SCOPE_VALUES = ("loopback", "compose_private")
CONTENT_CAPTURE_ENVS = ("development", "evaluation")


class AppConfig(BaseSettings):
    """仅从环境变量加载配置。

    构造函数执行 fail-closed（失败即关闭）校验：部署拓扑非法、缺失操作者身份
    或内容采集被禁止时立即抛错，使进程无法在不安全状态下启动。
    """

    # 同时支持从项目根目录启动和从 backend 目录启动；真实密钥只放在被 Git
    # 忽略的本地 .env 中，容器环境仍优先使用显式环境变量。
    model_config = SettingsConfigDict(extra="ignore", env_file=(".env", "../.env"))

    app_env: Literal["development", "evaluation", "production"] = "development"
    deployment_mode: Literal["single_user_private"] = "single_user_private"
    api_bind_scope: Literal["loopback", "compose_private"] = "loopback"
    internal_api_base_url: str = "http://127.0.0.1:8000"
    actor_id: str = ""

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/novel"
    llm_base_url: str = ""
    llm_api_key: str = ""
    model_name: str = ""
    default_token_budget: int = 8192

    audit_retention_days: int = 30
    checkpoint_retention_days: int = 7

    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = ""
    langsmith_capture_content: bool = False

    @model_validator(mode="after")
    def fail_closed(self) -> Self:
        """对配置做 fail-closed 校验，任何非法组合都立即抛 ValueError。

        校验点：deployment_mode 与 api_bind_scope 必须落在允许取值内；actor_id
        不得为空（客户端不能覆盖操作者身份）；loopback 模式下 internal_api_base_url
        必须指向 127.0.0.1 回环地址，compose_private 模式下必须为 http://api:8000；
        LANGSMITH_CAPTURE_CONTENT=true 仅允许在 development/evaluation 环境开启。
        返回：校验通过后返回自身。
        """
        if self.deployment_mode not in DEPLOYMENT_MODE_VALUES:
            raise ValueError(
                f"DEPLOYMENT_MODE must be one of {DEPLOYMENT_MODE_VALUES!r}; got {self.deployment_mode!r}"
            )
        if self.api_bind_scope not in API_BIND_SCOPE_VALUES:
            raise ValueError(
                f"API_BIND_SCOPE must be one of {API_BIND_SCOPE_VALUES!r}; got {self.api_bind_scope!r}"
            )
        if not self.actor_id.strip():
            raise ValueError("ACTOR_ID must be non-empty; the client cannot override the actor identity")
        if self.api_bind_scope == "loopback" and not self.internal_api_base_url.startswith("http://127.0.0.1:"):
            raise ValueError(
                "INTERNAL_API_BASE_URL must point to the loopback API in loopback mode "
                f"(got {self.internal_api_base_url!r})"
            )
        if self.api_bind_scope == "compose_private" and self.internal_api_base_url != "http://api:8000":
            raise ValueError(
                "INTERNAL_API_BASE_URL must be http://api:8000 in compose_private mode "
                f"(got {self.internal_api_base_url!r})"
            )
        if self.langsmith_capture_content and self.app_env not in CONTENT_CAPTURE_ENVS:
            raise ValueError(
                "LANGSMITH_CAPTURE_CONTENT=true is only allowed when APP_ENV is "
                f"'development' or 'evaluation'; got APP_ENV={self.app_env!r}"
            )
        return self


def get_config() -> AppConfig:
    """从当前环境读取并返回一个新的 AppConfig 实例（每次调用重新读取环境变量）。"""
    return AppConfig()

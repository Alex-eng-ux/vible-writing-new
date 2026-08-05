"""Hook 注册表模块。

`HookRegistry` 按 Agent 类型注册并选择生命周期钩子，同时持有 schema 校验、
错误处理与候选事实提取等单例钩子。公共提交守卫（`CommitGuardHook`）始终生效，
任何 Agent 都不能因缺少注册钩子而绕过它。
"""

from __future__ import annotations

from app.agents.hooks import (
    CommitGuardHook,
    ErrorHook,
    FactExtractionHook,
    LifecycleHook,
    SchemaHook,
)


class HookRegistry:
    """按 Agent 类型注册并选择生命周期钩子。

    公共提交守卫始终生效；任何 Agent 都不能因缺少注册钩子而绕过它。
    """

    def __init__(self) -> None:
        self._lifecycle: dict[str, list[LifecycleHook]] = {}
        self._schema = SchemaHook()
        self._error = ErrorHook()
        self._fact = FactExtractionHook()
        self._commit_guard: CommitGuardHook | None = None

    def register(self, agent_type: str, hook: LifecycleHook) -> None:
        """为指定 Agent 类型注册一个生命周期钩子。

        参数：
            agent_type: Agent 类型标识。
            hook: 要注册的钩子，会在该类型 Agent 运行前后被调用。
        """
        self._lifecycle.setdefault(agent_type, []).append(hook)

    def lifecycle(self, agent_type: str) -> list[LifecycleHook]:
        """返回指定 Agent 类型注册的生命周期钩子列表（副本）。

        参数：
            agent_type: Agent 类型标识。

        返回：该类型注册的钩子列表；未注册时返回空列表。
        """
        return list(self._lifecycle.get(agent_type, []))

    def set_commit_guard(self, hook: CommitGuardHook) -> None:
        """设置公共提交守卫钩子。

        参数：
            hook: `CommitGuardHook` 实例，供给所有正式提交节点使用。
        """
        self._commit_guard = hook

    def commit_guard(self) -> CommitGuardHook:
        """返回公共提交守卫钩子。

        返回：已配置的 `CommitGuardHook`。

        失败条件：未配置时抛出 `RuntimeError`，提示提交守卫未配置。
        """
        if self._commit_guard is None:
            raise RuntimeError("commit guard hook is not configured")
        return self._commit_guard

    @property
    def schema(self) -> SchemaHook:
        """返回 schema 校验钩子。"""
        return self._schema

    @property
    def error(self) -> ErrorHook:
        """返回异常处理钩子。"""
        return self._error

    @property
    def fact(self) -> FactExtractionHook:
        """返回候选事实提取钩子。"""
        return self._fact

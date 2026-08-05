"""Context 层的数据契约与端口定义。

本模块集中定义 Context Pack 组装所依赖的纯数据契约（TypedDict）与
依赖端口（Protocol），不包含任何业务逻辑或数据库访问。这些类型是
Context 层与上层（生成运行、候选写回）之间的稳定接口，任何字段变更
都必须保证向后兼容，否则会破坏序列化与 manifest 一致性校验。

核心约束：
- 所有类型均为只读数据形状，函数返回值不得私自扩展字段；
- ContextItem 的 priority 决定优先级（0 为强制项，>0 为可选项，
  越小越优先，见 composer 的预算分配逻辑）；
- SourceType 限定了可接受的来源类型；
- Protocol 端口定义检索与 manifest 的依赖边界，便于注入与测试。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, TypedDict


class SceneRequest(TypedDict):
    """场景生成请求的输入契约。

    描述一次生成运行针对的决策请求上下文，用于计算请求指纹并驱动
    元数据检索。

    参数：
        request_type: 请求类型，约束为 new_chapter/continue/rewrite/review。
        decision_target: 决策目标，可为 plan/scene/chapter/canon 或 None。
        scene_id: 目标场景 ID。
        base_scene_revision_id: 基准场景版本 ID，可为 None。
        base_chapter_revision_id: 基准章节版本 ID，可为 None。
    """

    request_type: Literal["new_chapter", "continue", "rewrite", "review"]
    decision_target: Literal["plan", "scene", "chapter", "canon", None]
    scene_id: str
    base_scene_revision_id: str | None
    base_chapter_revision_id: str | None


SourceType = Literal[
    "scene", "revision", "canon", "entity", "timeline", "plot_thread", "handoff", "style"
]


class ContextItem(TypedDict):
    """一条被纳入 Context Pack 的上下文来源。

    该条目由检索器（retrievers）或 composer 组装，供预算分配与
    manifest 登记使用。

    参数：
        source_id: 来源唯一标识（如场景 ID、版本 ID、实体 ID）。
        source_type: 来源类型，见 SourceType。
        source_revision_id: 来源对应的版本 ID，可为 None。
        priority: 优先级，0 为强制项、>0 为可选项，越小越优先。
        content: 实际送入上下文的内容文本。
        token_estimate: 内容预估 token 数（确定性估算）。
        truncation_reason: 被预算截断的原因，未截断时为 None。
        metadata: 附加元数据，默认空字典。
    """

    source_id: str
    source_type: SourceType
    source_revision_id: str | None
    priority: int
    content: str
    token_estimate: int
    truncation_reason: str | None
    metadata: dict


class ContextManifestEntry(TypedDict):
    """manifest 中登记的单条来源条目。

    记录来源 ID、类型、解析用到的版本 ID 与解析时间，用于版本基线
    一致性与回放校验。

    参数：
        source_id: 来源唯一标识。
        source_type: 来源类型。
        source_revision_id: 解析采用的来源版本 ID，可为 None。
        resolved_at: 版本解析时间。
    """

    source_id: str
    source_type: str
    source_revision_id: str | None
    resolved_at: datetime


class ContextManifest(TypedDict):
    """一次生成运行对应的来源清单（manifest）。

    记录请求指纹、来源条目集合以及跨章节交接（handoff）引用，用于
    校验同一运行内的来源集合/版本基线/交接链是否一致，防止重复生成
    时静默覆盖或结果漂移。

    参数：
        manifest_id: 持久化 manifest 行 ID。
        generation_run_id: 所属生成运行 ID。
        request_fingerprint: 请求指纹（SHA-256）。
        entries: 登记来源条目列表。
        entry_handoff_id: 跨章节交接条目 ID，可为 None。
        entry_source_chapter_revision_id: 交接来源章节版本 ID，可为 None。
        entry_handoff_chain_hash: 交接链哈希，可为 None。
    """

    manifest_id: str
    generation_run_id: str
    request_fingerprint: str
    entries: list[ContextManifestEntry]
    entry_handoff_id: str | None
    entry_source_chapter_revision_id: str | None
    entry_handoff_chain_hash: str | None


class ContextPack(TypedDict):
    """一次生成运行组装得到的最终上下文包。

    组装完成后返回给上层，用于生成运行；不包含任何写回动作。

    参数：
        generation_run_id: 所属生成运行 ID。
        scene_id: 目标场景 ID。
        items: 最终入选（含强制项与预算内可选项）的上下文条目。
        total_token_estimate: 入选条目的总 token 预估值。
        omitted_source_ids: 因预算不足被截断的来源 ID 列表。
        manifest_id: 本次运行对应 manifest 的 ID。
    """

    generation_run_id: str
    scene_id: str
    items: list[ContextItem]
    total_token_estimate: int
    omitted_source_ids: list[str]
    manifest_id: str


class ContextManifestPort(Protocol):
    """manifest 持久化端口，定义创建/复用与回放校验的依赖边界。

    由 manifest 模块实现，供 composer 依赖注入，便于替换与测试。
    """

    def create_or_reuse(
        self,
        generation_run_id: str,
        request_fingerprint: str,
        entries: list[ContextManifestEntry],
        entry_handoff_id: str | None,
        entry_source_chapter_revision_id: str | None,
        entry_handoff_chain_hash: str | None,
    ) -> ContextManifest: ...

    def validate_replay(
        self,
        generation_run_id: str,
        manifest: ContextManifest,
        request_fingerprint: str,
    ) -> None: ...


class MetadataRetriever(Protocol):
    """元数据检索端口，定义按请求与版本白名单检索上下文的依赖边界。

    由 SQL 检索器实现，供 composer 注入；实现不得越出白名单范围。
    """

    def retrieve(self, request: SceneRequest, source_revision_ids: list[str]) -> list[ContextItem]: ...


class VectorRetriever(Protocol):
    """向量补充检索端口，定义按允许来源白名单补充上下文的依赖边界。

    由向量检索器实现；向量服务不可用时应降级为空结果而非硬失败。
    """

    def retrieve(self, query: str, allowed_source_ids: list[str], limit: int) -> list[ContextItem]: ...

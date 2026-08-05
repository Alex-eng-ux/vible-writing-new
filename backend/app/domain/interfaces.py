"""领域层类型契约：权威业务规则、版本、身份与 fencing 的共享类型。

本模块集中定义领域层内部通行的 TypedDict 与 Protocol。这些类型承载
“命令上下文”契约（身份互斥、租约、fencing、基线、幂等），并定义
CommitGuard 与读取端口等抽象接口，供领域实现与上层服务共同依赖。
"""

from typing import Literal, Protocol, TypedDict


class LeaseContext(TypedDict):
    """工作进程写入租约的上下文。

    worker_id: 持有租约的工作进程 id。
    fencing_token: 租约令牌，用于防止过期租约写库（fencing）。
    """

    worker_id: str
    fencing_token: int


class RunWriteFence(TypedDict):
    """运行写入 fencing 令牌（针对 api_command 或 worker 所有者）。

    generation_run_id: 该 fence 绑定的生成运行 id。
    owner_kind: 所有者类型，worker 或 api_command。
    owner_id: 所有者 id。
    fencing_token: 递增令牌，用于检测已被后续写入取代的过期 fence。
    """

    generation_run_id: str
    owner_kind: Literal["worker", "api_command"]
    owner_id: str
    fencing_token: int


class CommandContext(TypedDict):
    """完整的命令上下文，承载一次写命令的身份、基线与 fencing 信息。

    关键约束：
    - generation_run_id 与 manual_command_id 互斥（身份互斥）。
    - agent/review 来源必须携带 worker lease；author 来源必须携带
      manual_command_id 且不得携带运行身份或租约。
    - idempotency_key 必须非空，用于幂等去重。
    """

    lease_context: LeaseContext | None
    write_fence: RunWriteFence | None
    generation_run_id: str | None
    agent_run_id: str | None
    manual_command_id: str | None
    source: Literal["author", "agent", "review"] | None
    parent_generation_run_id: str | None
    supersedes_run_id: str | None
    parent_plan_revision_id: str | None
    actor_id: str
    preceding_chapter_id: str | None
    preceding_accepted_chapter_revision_id: str | None
    entry_handoff_id: str | None
    entry_source_chapter_revision_id: str | None
    entry_handoff_chain_hash: str | None
    base_scene_revision_id: str | None
    base_chapter_revision_id: str | None
    accepted_scene_revision_id: str | None
    accepted_chapter_revision_id: str | None
    plan_revision_id: str | None
    canon_scope: Literal["chapter", "scene"] | None
    decision_target: Literal["plan", "scene", "chapter", "canon"] | None
    context_source_refs: list[str]
    author_decision: Literal["accept", "feedback", "cancel"] | None
    idempotency_key: str
    expected_run_version: int | None


class ResourceCommandContext(TypedDict):
    """资源根节点（project/volume 等）创建所需的精简命令上下文。

    资源创建不涉及运行身份，只要求 actor_id 与幂等键。
    """

    actor_id: str
    idempotency_key: str


class ManualChangeSetContext(TypedDict):
    """作者（手动）ChangeSet 命令上下文的固定形状。

    固定 generation_run_id 与 write_fence 为 None，source 恒为 author，
    仅携带 manual_command_id、actor_id 与幂等键。
    """

    generation_run_id: None
    write_fence: None
    manual_command_id: str
    source: Literal["author"]
    actor_id: str
    idempotency_key: str
    expected_run_version: None


ChangeSetCommandContext = CommandContext | ManualChangeSetContext


class TextOperation(TypedDict):
    """文本操作原语（op 与可选 value）。"""

    op: str
    value: str | None


class CommitGuardPort(Protocol):
    """CommitGuard 的端口抽象，供依赖注入或测试替身使用。"""

    def validate(
        self,
        operation: str,
        actor_id: str,
        base_revision_id: str | None,
        idempotency_key: str,
        source_refs: list[str],
        generation_run_id: str | None = None,
        manual_command_id: str | None = None,
        expected_run_version: int | None = None,
        operation_format: str | None = None,
        base_content_hash: str | None = None,
        lease_context: LeaseContext | None = None,
        write_fence: RunWriteFence | None = None,
    ) -> None: ...


class RunWriteFencePort(Protocol):
    """声明 api_command 写入 fencing 并校验其有效性的端口抽象。"""

    def claim_api_command(
        self, generation_run_id: str, manual_command_id: str, expected_run_version: int
    ) -> RunWriteFence: ...
    def validate(self, write_fence: RunWriteFence) -> None: ...


class ChapterHandoff(TypedDict):
    """章节交接的投影视图，供领域读取端返回。"""

    id: str
    chapter_id: str
    source_chapter_revision_id: str
    entry_handoff_status: str
    chain_hash: str
    status: str


class ChapterHandoffReadPort(Protocol):
    """章节交接读取端口的抽象，返回有效交接或 None。"""

    def get_valid_entry(
        self,
        chapter_id: str,
        handoff_id: str | None,
        source_chapter_revision_id: str | None,
        expected_chain_hash: str | None,
    ) -> ChapterHandoff | None: ...

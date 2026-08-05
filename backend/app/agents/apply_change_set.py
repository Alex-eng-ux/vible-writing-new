"""ChangeSet 应用模块。

`apply_change_set` 在固定的基线快照上临时应用语义化文本操作（semantic_text），
返回新文本与候选事实。边界：绝不覆盖已确认的正文；遇到冲突或缺少基线时抛出
异常，交由调用方路由到反馈。实际应用属于图边界外的领域职责，本函数仅用于
占位/验证。
"""

from __future__ import annotations

from app.agents.schemas import RevisionOutput
from app.errors import AppError


def apply_change_set(
    baseline_text: str,
    revision: RevisionOutput,
    base_content_hash: str,
) -> tuple[str, list[dict]]:
    """在固定基线快照上临时应用 semantic_text 操作。

    参数：
        baseline_text: 基线场景文本。
        revision: 修订输出，包含 `base_scene_revision_id`、`operation_format`
            与文本操作列表。
        base_content_hash: 基线内容哈希（用于校验基线一致性，当前为占位）。

    返回：
        `(new_text, candidate_facts)`：应用操作后的新文本与候选事实字典列表。

    失败条件：
        - 缺少 `base_scene_revision_id` 时抛出 `SCENE_STATE_INCOMPATIBLE`。
        - `operation_format` 不是 "semantic_text" 时抛出
          `COMMAND_CONTEXT_MISMATCH`。
        - replace/delete 操作的锚点（`old_text`）在基线文本中不存在时抛出
          `SCENE_STALE`，表示基线不匹配，调用方应路由到反馈。

    关键约束：绝不覆盖已确认的正文；只基于传入的基线快照生成新文本。
    """
    if revision.base_scene_revision_id is None:
        raise AppError("SCENE_STATE_INCOMPATIBLE", "cannot apply a ChangeSet without a baseline")
    if revision.operation_format != "semantic_text":
        raise AppError("COMMAND_CONTEXT_MISMATCH", "only semantic_text operations are supported")
    text = baseline_text
    for op in revision.operations:
        if op.op == "replace":
            if op.old_text and op.old_text not in text:
                raise AppError("SCENE_STALE", "baseline text does not match the patch anchor")
            text = text.replace(op.old_text, op.new_text, 1) if op.old_text else text + op.new_text
        elif op.op == "insert":
            text += op.new_text
        elif op.op == "delete":
            if op.old_text and op.old_text not in text:
                raise AppError("SCENE_STALE", "baseline text does not match the patch anchor")
            text = text.replace(op.old_text, "", 1) if op.old_text else text
    candidate_facts = [c.model_dump() for c in revision.candidate_facts]
    return text, candidate_facts

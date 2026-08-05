"""Agent 结果路由模块。

`AgentResultRouter` 把各 Agent 的结构化输出归一化为 `RouterOutcome`，决定
后续流转方向：继续到下游节点、暂停等待作者澄清、反馈、取消或失败。路由
决策是图边界的核心逻辑，需保证 `needs_clarification` 状态携带 `pending_node`
与问题，且绝不流向下游节点。
"""

from __future__ import annotations

from pydantic import BaseModel

from ..errors import AppError
from .schemas import RouterOutcome


class AgentResultRouter:
    """将 Agent 输出归一化为 `RouterOutcome`。

    处理 continue、等待澄清、反馈、取消与失败等状态。`needs_clarification`
    结果必须携带 `pending_node` 与问题，且绝不继续到下游节点。
    """

    def route(self, output: BaseModel, agent_type: str, pending_node: str) -> RouterOutcome:
        """把 Agent 输出路由为 `RouterOutcome`。

        参数：
            output: Agent 输出的结构化模型。
            agent_type: Agent 类型标识（writing / continuity / review / revision）。
            pending_node: 需要暂停时挂起的节点名。

        返回：`RouterOutcome`，指示继续/暂停/反馈/取消等去向。

        失败条件：状态为 `needs_clarification` 但问题列表为空时抛出
        `COMMAND_CONTEXT_MISMATCH` 错误。

        关键决策：
        - `needs_clarification` 一律暂停，绝不继续。
        - 未知的 `agent_type` 走 `_default_next` 默认路由。
        """
        status = getattr(output, "status", None)
        if status == "needs_clarification":
            questions = list(getattr(output, "clarification_questions", []))
            if not questions:
                raise AppError(
                    "COMMAND_CONTEXT_MISMATCH",
                    f"{agent_type} needs_clarification requires non-empty questions",
                )
            return RouterOutcome(
                status="needs_clarification",
                pending_node=pending_node,
                clarification_questions=questions,
            )
        if agent_type == "writing":
            return self._route_writing(output, pending_node)
        if agent_type == "continuity":
            return self._route_continuity(output, pending_node)
        if agent_type == "review":
            return self._route_review(output, pending_node)
        if agent_type == "revision":
            return self._route_revision(output, pending_node)
        if agent_type == "chapter_planner":
            return RouterOutcome(status="continue", next_node="chapter_review")
        if agent_type == "chapter_review":
            return RouterOutcome(status="continue", next_node="chapter_aggregator")
        if agent_type == "canon":
            # Task 4C：CanonAgent 输出归一化后进入作者逐条确认（canon_confirmation），
            # 只有作者确认后才由正式提交节点写正式 Canon。
            return RouterOutcome(status="continue", next_node="canon_confirmation")
        return RouterOutcome(status="continue", next_node=_default_next(agent_type))

    def _route_writing(self, output: BaseModel, pending_node: str) -> RouterOutcome:
        """写作输出路由：继续到 continuity 节点。"""
        return RouterOutcome(status="continue", next_node="continuity")

    def _route_continuity(self, output: BaseModel, pending_node: str) -> RouterOutcome:
        """连续性检查输出路由。

        - `needs_author_confirmation`：转为 `feedback` 暂停，等待作者确认。
        - 其余（含 `issues`）继续到 review 节点。
        """
        status = getattr(output, "status", None)
        if status == "needs_author_confirmation":
            return RouterOutcome(
                status="feedback",
                pending_node=pending_node,
                clarification_questions=list(getattr(output, "clarification_questions", [])),
            )
        if status == "issues":
            return RouterOutcome(status="continue", next_node="review")
        return RouterOutcome(status="continue", next_node="review")

    def _route_review(self, output: BaseModel, pending_node: str) -> RouterOutcome:
        """评审输出路由。

        存在 high / blocking 级别的高风险 `review_issues` 时转为 `feedback`
        暂停，要求作者反馈；否则继续到 revision 节点。
        """
        high_risk = [
            i
            for i in getattr(output, "review_issues", [])
            if getattr(i, "severity", "") in ("high", "blocking")
        ]
        if high_risk:
            return RouterOutcome(
                status="feedback",
                pending_node=pending_node,
                clarification_questions=["high-risk review issues require author feedback"],
            )
        return RouterOutcome(status="continue", next_node="revision")

    def _route_revision(self, output: BaseModel, pending_node: str) -> RouterOutcome:
        """修订输出路由：继续到 apply_change_set 节点。"""
        return RouterOutcome(status="continue", next_node="apply_change_set")


def _default_next(agent_type: str) -> str:
    """返回未知 Agent 类型时的默认下游节点。"""
    return {
        "writing": "continuity",
        "continuity": "review",
        "review": "revision",
        "revision": "apply_change_set",
    }.get(agent_type, "end")

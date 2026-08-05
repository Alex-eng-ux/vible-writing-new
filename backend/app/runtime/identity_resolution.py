"""身份解析：把可信的 local_key、文本定位符与来源引用解析为正式 ID。

本模块负责运行期身份解析规则，关键约束：
- 只有运行时才能分配正式 ID、anchor_id 与哈希；模型绝不能自行铸造正式 ID；
- 缺失正式 ID 的引用解析为 needs_clarification（通过抛错由上层处理）；
- local_key 必须有对应的正式 ID 映射，否则视为上下文不匹配；
- source_ref 必须存在于当前清单来源集合中，否则拒绝。
"""
from __future__ import annotations

import hashlib
import json

from app.errors import AppError


class IdentityResolutionStep:
    """把可信的 local_key、文本定位符与来源引用解析为正式 ID。

    只有运行时才能分配正式 ID、anchor_id 与哈希；模型绝不能自行铸造正式 ID。
    缺失正式 ID 会解析为 needs_clarification。
    """

    def resolve_local_key(self, local_key: str, formal_ids: dict[str, str]) -> str:
        """把 local_key 解析为正式 ID（若有映射）。

        参数:
            local_key: 模型给出的本地键。
            formal_ids: local_key 到正式 ID 的映射。

        返回:
            对应的正式 ID。

        失败条件: local_key 不存在映射时抛出 ``COMMAND_CONTEXT_MISMATCH``
        （整理为 needs_clarification 场景）。
        """
        if local_key in formal_ids:
            return formal_ids[local_key]
        raise AppError(
            "COMMAND_CONTEXT_MISMATCH",
            f"local_key has no formal ID mapping: {local_key}",
        )

    def anchor_hash(self, text: str) -> str:
        """计算文本的 anchor 哈希（带 ``sha256:`` 前缀）。

        参数:
            text: 待哈希文本。

        返回:
            形如 ``sha256:<hexdigest>`` 的 anchor 哈希。
        """
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    def text_hash(self, text: str) -> str:
        """计算文本的裸 SHA-256 哈希（无前缀）。

        参数:
            text: 待哈希文本。

        返回:
            十六进制 SHA-256 摘要。
        """
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def resolve_source_ref(self, source_ref: str, allowed_source_ids: list[str]) -> str:
        """校验来源引用是否属于当前清单。

        参数:
            source_ref: 引用的来源 ID。
            allowed_source_ids: 当前清单允许的来源 ID 集合。

        返回:
            校验通过时的 source_ref。

        失败条件: source_ref 不在 allowed_source_ids 中时抛出
        ``COMMAND_CONTEXT_MISMATCH``。
        """
        if source_ref not in allowed_source_ids:
            raise AppError(
                "COMMAND_CONTEXT_MISMATCH",
                f"source ref is not in the current manifest: {source_ref}",
            )
        return source_ref

    def stable_signature(self, payload: dict) -> str:
        """计算负载的稳定签名（规范化 JSON 的 SHA-256）。

        参数:
            payload: 待签名负载。

        返回:
            十六进制 SHA-256 摘要。通过对键排序并使用紧凑分隔符，保证相同语义
            负载得到相同签名（用于幂等/指纹比对）。
        """
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

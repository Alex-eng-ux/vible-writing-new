"""图内上下文清单（ContextManifest）操作步骤。

本模块是图内的薄封装，调用 Task 3 的 ``ContextManifestPort``（``app.context.manifest``）
完成清单的创建/复用与重放校验，不重新定义清单或来源解析逻辑。关键约束：
- ``create_or_reuse`` 依据 request_fingerprint 决定复用已有清单还是新建；
- ``validate_replay`` 在校验重放时校验指纹与清单的一致性，防止跨请求重放误解。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.context import manifest as manifest_mod
from app.context.models import ContextManifest, ContextManifestEntry


class ContextManifestStep:
    """在图中调用 Task 3 的 ContextManifestPort。

    不重新定义清单或来源解析逻辑。
    """

    def __init__(self, session: Session) -> None:
        """初始化。

        参数:
            session: 数据库会话，用于清单读写。
        """
        self._session = session

    def create_or_reuse(
        self,
        generation_run_id: str,
        request_fingerprint: str,
        entries: list[ContextManifestEntry],
        entry_handoff_id: str | None,
        entry_source_chapter_revision_id: str | None,
        entry_handoff_chain_hash: str | None,
    ) -> ContextManifest:
        """创建或复用上下文清单。

        参数:
            generation_run_id: 目标运行 ID。
            request_fingerprint: 请求指纹，用于判断是否可复用已有清单。
            entries: 清单条目列表。
            entry_handoff_id: 可选的交接 ID。
            entry_source_chapter_revision_id: 可选的来源章节修订 ID。
            entry_handoff_chain_hash: 可选的交接链哈希。

        返回:
            创建或复用得到的 ``ContextManifest``。

        约束: 实际创建/复用与指纹校验逻辑委托给 ``app.context.manifest``，本类
        仅做透传。
        """
        return manifest_mod.create_or_reuse(
            self._session,
            generation_run_id,
            request_fingerprint,
            entries,
            entry_handoff_id,
            entry_source_chapter_revision_id,
            entry_handoff_chain_hash,
        )

    def validate_replay(
        self,
        generation_run_id: str,
        manifest: ContextManifest,
        request_fingerprint: str,
    ) -> None:
        """校验清单重放与请求指纹的一致性。

        参数:
            generation_run_id: 目标运行 ID。
            manifest: 待校验的清单。
            request_fingerprint: 请求指纹。

        失败条件: 指纹与清单不一致时由底层逻辑抛出错误（防止跨请求重放误解）。
        """
        manifest_mod.validate_replay(self._session, generation_run_id, manifest, request_fingerprint)

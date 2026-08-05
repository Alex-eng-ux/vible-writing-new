"""ProseMirror 文档操作领域：空基线文档与应用 prosemirror_step 操作。

作者手工编辑以 ``prosemirror_step`` 保存，必须以规范化空 ProseMirror 文档
``{"type": "doc", "content": []}`` 作为首稿基线，并把操作真正应用到基线文档
上得到落盘内容；绝不能把应用结果写成占位字符串 ``"applied"``，也不能丢失
作者输入的内容。规范化 JSON 使用稳定 UTF-8 编码（键按字典序、无额外空白）
以保证内容哈希跨环境稳定。
"""

from __future__ import annotations

import hashlib
import json

from ..errors import AppError

EMPTY_DOC = {"type": "doc", "content": []}


def _canonical_json(doc: dict) -> str:
    """把文档规范化为 UTF-8 JSON：键排序、无多余空白，保证哈希稳定。"""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def empty_doc_content() -> str:
    """返回首稿基线使用的规范化空 ProseMirror 文档，作为版本基线。"""
    return _canonical_json(EMPTY_DOC)


def empty_doc_hash() -> str:
    """返回规范化空 ProseMirror 文档的 SHA-256，用于基线指纹匹配。"""
    return hashlib.sha256(empty_doc_content().encode("utf-8")).hexdigest()


def _ensure_paragraph(doc: dict) -> dict:
    """保证文档至少有一个段落，返回用于追加内联内容的最后一个段落。"""
    paragraphs = doc.setdefault("content", [])
    if not paragraphs or not isinstance(paragraphs[-1], dict) or paragraphs[-1].get("type") != "paragraph":
        paragraphs.append({"type": "paragraph", "content": []})
        doc["content"] = paragraphs
    return paragraphs[-1]


def apply_prosemirror_steps(base_content: str, operations: list[dict]) -> str:
    """把有序 prosemirror_step 操作应用到基线文档，返回规范化 JSON。

    参数：base_content 为基线文档的规范化 JSON；operations 为有序操作列表。
    返回：应用操作后的规范化 JSON 文档。

    支持的操作（文档级最小解释器）：
        - insert：向最后一个段落追加给定文本（合并到末尾文本节点）。
        - replace：用给定文本替换全部正文。
        - delete：清空正文。
    解析失败或遇到不支持的操作时抛 AppError，保证不静默丢弃作者内容。

    失败条件：基线不是合法 ProseMirror 文档抛 SCENE_STATE_INCOMPATIBLE；
    遇到未支持的操作抛 COMMAND_CONTEXT_MISMATCH。
    """
    doc: dict = json.loads(base_content) if base_content else {"type": "doc", "content": []}
    if not isinstance(doc, dict) or doc.get("type") != "doc":
        raise AppError("SCENE_STATE_INCOMPATIBLE", "invalid prosemirror document")

    for op in operations:
        op_type = op.get("op")
        value = op.get("value") or ""
        if op_type == "insert":
            paragraph = _ensure_paragraph(doc)
            inline = paragraph.setdefault("content", [])
            if inline and isinstance(inline[-1], dict) and inline[-1].get("type") == "text":
                inline[-1]["text"] = inline[-1].get("text", "") + value
            else:
                inline.append({"type": "text", "text": value})
        elif op_type == "replace":
            doc["content"] = [{"type": "paragraph", "content": [{"type": "text", "text": value}]}]
        elif op_type == "delete":
            doc["content"] = [{"type": "paragraph", "content": []}]
        else:
            raise AppError("COMMAND_CONTEXT_MISMATCH", f"unsupported prosemirror_step op: {op_type}")

    return _canonical_json(doc)

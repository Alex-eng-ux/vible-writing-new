"use client";

/**
 * Task 7A Tiptap/ProseMirror 正文编辑器。
 *
 * 约束：
 * - 展示服务端已接受的正文（规范化 ProseMirror JSON）；
 * - 只把编辑器纯文本变化上报给父组件，绝不直接更新正文或乐观修改
 *   accepted 版本；保存必须经 `source=author` ChangeSet 接口；
 * - 切换场景/基线刷新时用 `setContent` 重置，不丢失受控文档。
 */

import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Extension } from "@tiptap/core";
import { keymap } from "@tiptap/pm/keymap";
import { selectAll } from "@tiptap/pm/commands";
import { useEffect, useRef } from "react";

// StarterKit v3 未注册 Ctrl+A（Mod-a）键盘映射，全选会落到浏览器 DOM 选区
// （点击编辑器外按钮即丢失）。补一个 ProseMirror keymap：Ctrl/Cmd+A → selectAll，
// 使 onSelectionUpdate 能上报完整选中文本。
const SelectAllKeymap = Extension.create({
  name: "selectAllKeymap",
  addProseMirrorPlugins() {
    return [keymap({ "Mod-a": selectAll })];
  },
});

function parseDoc(doc: string | null): object {
  if (!doc) return { type: "doc", content: [] };
  try {
    return JSON.parse(doc) as object;
  } catch {
    return { type: "doc", content: [] };
  }
}

type Props = {
  /** 服务端正文（规范化 ProseMirror JSON 字符串），null 表示尚无 accepted 版本。 */
  doc: string | null;
  /** 编辑器内容（纯文本）变化回调，用于父组件计算基线哈希与操作。 */
  onChange?: (text: string) => void;
  /** 只读模式（版本比较/冲突展示使用）。 */
  readOnly?: boolean;
  /** 选中文本（ProseMirror selection 区间文本）变化回调；续写/改写按钮依赖。 */
  onSelectionText?: (text: string) => void;
  /** 编辑器实例就绪回调（续写/改写按钮需要同步读取当前选区文本，避免 React
   *  state 尚未 flush 时读到空值）。 */
  onEditorReady?: (editor: Editor) => void;
};

export default function ManuscriptEditor({ doc, onChange, readOnly = false, onSelectionText, onEditorReady }: Props) {
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const onSelectionRef = useRef(onSelectionText);
  onSelectionRef.current = onSelectionText;
  const onEditorReadyRef = useRef(onEditorReady);
  onEditorReadyRef.current = onEditorReady;

  const editor = useEditor({
    // StarterKit v3 不注册 Ctrl+A（Mod-a）键盘映射，需显式加入 SelectAllKeymap
    // 扩展，否则全选走浏览器 DOM 选区（点击编辑器外按钮即丢失），选中片段无法上报。
    extensions: [StarterKit, SelectAllKeymap],
    content: parseDoc(doc),
    editable: !readOnly,
    // Next.js App Router 服务端渲染下禁止立即渲染，避免 hydration 不一致。
    immediatelyRender: false,
    onCreate: ({ editor: e }) => {
      // 编辑器创建完成后上报实例，供父组件同步读取选区（状态更新有延迟）。
      onEditorReadyRef.current?.(e);
    },
    onUpdate: ({ editor: e }) => {
      onChangeRef.current?.(e.getText());
    },
    // ProseMirror selection（Ctrl+A / 拖选）独立于 DOM 选区；点击编辑器外按钮
    // 时 DOM selection 会被清除，因此必须从这里上报选中文本。
    onSelectionUpdate: ({ editor: e }) => {
      const { from, to } = e.state.selection;
      const text = e.state.doc.textBetween(from, to, "\n");
      onSelectionRef.current?.(text);
    },
  });

  // 基线/场景切换时重置文档（保持与服务器版本一致后再编辑）。
  const lastDoc = useRef(doc);
  useEffect(() => {
    if (doc !== lastDoc.current) {
      lastDoc.current = doc;
      editor?.commands.setContent(parseDoc(doc));
    }
  }, [doc, editor]);

  const toolbar = [
    { label: "B", title: "粗体", onClick: () => editor?.chain().focus().toggleBold().run(), active: editor?.isActive("bold") },
    { label: "I", title: "斜体", onClick: () => editor?.chain().focus().toggleItalic().run(), active: editor?.isActive("italic") },
    { label: "H1", title: "一级标题", onClick: () => editor?.chain().focus().toggleHeading({ level: 1 }).run(), active: editor?.isActive("heading", { level: 1 }) },
    { label: "¶", title: "段落", onClick: () => editor?.chain().focus().setParagraph().run(), active: editor?.isActive("paragraph") },
  ];

  return (
    <div className="editor" data-testid="manuscript-editor">
      {!readOnly && (
        <div className="editor-toolbar" data-testid="editor-toolbar">
          {toolbar.map((btn) => (
            <button
              key={btn.title}
              type="button"
              title={btn.title}
              className={btn.active ? "toolbar-btn active" : "toolbar-btn"}
              onMouseDown={(e) => {
                e.preventDefault();
                btn.onClick();
              }}
            >
              {btn.label}
            </button>
          ))}
        </div>
      )}
      <EditorContent editor={editor} className="editor-content" />
    </div>
  );
}

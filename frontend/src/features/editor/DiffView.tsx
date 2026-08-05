"use client";

/**
 * Task 7A 版本差异视图：按行 LCS 计算两个版本正文的差异并高亮展示。
 *
 * 约束：只做只读展示（新增/删除/未变行），不提供任何写操作。
 */

export type DiffLine = { kind: "same" | "added" | "removed"; text: string };

/** 按行 LCS 计算 a -> b 的差异标记。 */
export function diffLines(a: string, b: string): DiffLine[] {
  const left = a.split("\n");
  const right = b.split("\n");
  const n = left.length;
  const m = right.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] =
        left[i] === right[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (left[i] === right[j]) {
      lines.push({ kind: "same", text: left[i] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      lines.push({ kind: "removed", text: left[i] });
      i += 1;
    } else {
      lines.push({ kind: "added", text: right[j] });
      j += 1;
    }
  }
  while (i < n) {
    lines.push({ kind: "removed", text: left[i] });
    i += 1;
  }
  while (j < m) {
    lines.push({ kind: "added", text: right[j] });
    j += 1;
  }
  return lines;
}

type Props = {
  left: string;
  right: string;
  leftLabel: string;
  rightLabel: string;
};

export default function DiffView({ left, right, leftLabel, rightLabel }: Props) {
  const lines = diffLines(left, right);
  const added = lines.filter((l) => l.kind === "added").length;
  const removed = lines.filter((l) => l.kind === "removed").length;
  return (
    <div className="diff-view" data-testid="diff-view">
      <div className="diff-summary" data-testid="diff-summary">
        {leftLabel} → {rightLabel}：新增 {added} 行 / 删除 {removed} 行
      </div>
      <div className="diff-body">
        {lines.map((line, idx) => (
          <div key={idx} className={`diff-line diff-${line.kind}`} data-kind={line.kind}>
            <span className="diff-marker">{line.kind === "added" ? "+" : line.kind === "removed" ? "-" : " "}</span>
            <span className="diff-text">{line.text || " "}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

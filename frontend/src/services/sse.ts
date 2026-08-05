/**
 * Task 7B SSE 客户端：订阅运行事件流。
 *
 * 约束：
 * - 使用 fetch + ReadableStream 手动解析（可以控制 `Last-Event-ID` 请求头，
 *   浏览器 EventSource 无法自定义请求头）；
 * - 断线自动重连：重试时携带最后一次成功收到的事件 id（`run_id:sequence`），
 *   服务端从下一序号补发，保证不丢事件；
 * - 按事件 id 去重：重放与实时推送可能交叠，同一 id 只回调一次；
 * - 心跳帧（`: heartbeat`）被忽略；连接关闭后不再重试（调用方显式断开）。
 */

export type SseEvent = {
  id: string;
  event: string;
  data: string;
};

export type SseOptions = {
  /** 收到事件（已去重）回调。 */
  onEvent: (event: SseEvent) => void;
  /** 连接状态变化回调（测试断言/UI 提示用）。 */
  onStatus?: (status: "connecting" | "open" | "closed") => void;
  /** 重连最大退避（毫秒），默认 8000。 */
  maxRetryMs?: number;
};

const DEFAULT_MAX_RETRY_MS = 8000;

/** 解析一段 SSE 帧（可能含多条事件），返回事件列表。 */
function parseSseFrames(chunk: string): SseEvent[] {
  const frames: SseEvent[] = [];
  for (const raw of chunk.split("\n\n")) {
    const lines = raw.split("\n");
    let id = "";
    let event = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("id:")) {
        id = line.slice(3).trim();
      } else if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      } else if (line.startsWith(":")) {
        // 注释帧（heartbeat）忽略。
      }
    }
    if (id || dataLines.length > 0) {
      frames.push({ id, event, data: dataLines.join("\n") });
    }
  }
  return frames;
}

/**
 * 订阅运行事件流。返回断开函数；组件卸载/切换场景时必须调用。
 */
export function connectRunEvents(runId: string, options: SseOptions): () => void {
  const maxRetryMs = options.maxRetryMs ?? DEFAULT_MAX_RETRY_MS;
  const seen = new Set<string>();
  let lastEventId = "";
  let closed = false;
  let retryMs = 1000;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  async function open(): Promise<void> {
    if (closed) return;
    options.onStatus?.("connecting");
    const headers: Record<string, string> = {};
    if (lastEventId) {
      headers["Last-Event-ID"] = lastEventId;
    }
    let res: Response;
    try {
      res = await fetch(`/api/runs/${runId}/events`, { headers });
    } catch {
      scheduleRetry();
      return;
    }
    if (!res.ok || !res.body) {
      scheduleRetry();
      return;
    }
    options.onStatus?.("open");
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const newlineIndex = buffer.lastIndexOf("\n\n");
        if (newlineIndex === -1) continue;
        const complete = buffer.slice(0, newlineIndex);
        buffer = buffer.slice(newlineIndex + 2);
        for (const event of parseSseFrames(complete)) {
          if (!event.id) continue;
          if (seen.has(event.id)) continue; // 按事件 id 去重
          seen.add(event.id);
          lastEventId = event.id; // 记录最后序号，断线重连时作为 Last-Event-ID
          try {
            options.onEvent(event);
          } catch {
            // 回调异常不中断事件流。
          }
        }
      }
    } catch {
      // 网络错误/流中断：退避重连。
    } finally {
      reader.releaseLock();
    }
    if (!closed) {
      scheduleRetry();
    }
  }

  function scheduleRetry(): void {
    if (closed || retryTimer) return;
    retryTimer = setTimeout(() => {
      retryTimer = null;
      retryMs = Math.min(retryMs * 2, maxRetryMs);
      void open();
    }, retryMs);
  }

  void open();

  return () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    options.onStatus?.("closed");
  };
}

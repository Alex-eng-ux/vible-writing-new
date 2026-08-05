/**
 * Task 7B SSE 转发端点：`/api/runs/{runId}/events`。
 *
 * 为什么需要独立转发：Next.js 的 `rewrites` 代理在 dev 模式下会缓冲
 * `text/event-stream` 响应体（无限流不实时转发），浏览器无法实时收到事件。
 * 本 route handler 位于文件系统路由（afterFiles rewrites 之前匹配），显式
 * 用 ReadableStream 将后端事件流逐块透传，并透传 `Last-Event-ID` 请求头以
 * 支持断线重放。
 *
 * 约束：
 * - 只转发 GET；`Last-Event-ID` 透传后端；响应头保持 `text/event-stream`；
 * - 后端不可用时返回 502，前端 SSE 客户端退避重连。
 */

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ runId: string }> },
): Promise<Response> {
  const { runId } = await params;
  const backendBase = process.env.INTERNAL_API_BASE_URL || "http://127.0.0.1:8000";
  const headers: Record<string, string> = {};
  const lastEventId = request.headers.get("last-event-id");
  if (lastEventId) {
    headers["Last-Event-ID"] = lastEventId;
  }

  const upstream = await fetch(`${backendBase}/api/runs/${runId}/events`, {
    headers,
    cache: "no-store",
  }).catch(() => null);
  if (!upstream || !upstream.ok || !upstream.body) {
    return new Response("upstream unavailable", { status: upstream ? upstream.status : 502 });
  }

  // 逐块透传后端 SSE 流（不缓冲），保证事件实时到达浏览器。
  const reader = upstream.body.getReader();
  const stream = new ReadableStream<Uint8Array>({
    async pull(controller) {
      const { done, value } = await reader.read();
      if (done) {
        controller.close();
        return;
      }
      controller.enqueue(value);
    },
    cancel() {
      void reader.cancel();
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}

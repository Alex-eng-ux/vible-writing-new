/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // 关闭 Next.js 响应压缩：其压缩中间件会缓冲 `text/event-stream` 无限流
  // （客户端声明 Accept-Encoding: gzip 时读不到任何数据），SSE 实时事件必须
  // 绕过压缩层逐块透传。
  compress: false,
  async rewrites() {
    const baseUrl = process.env.INTERNAL_API_BASE_URL || "http://127.0.0.1:8000";
    return [
      { source: "/api/health", destination: `${baseUrl}/health` },
      { source: "/api/ready", destination: `${baseUrl}/ready` },
      // Task 7A：所有业务 API 经 Next.js 代理，前端不直接访问后端端口。
      { source: "/api/:path*", destination: `${baseUrl}/api/:path*` },
    ];
  },
};

export default nextConfig;
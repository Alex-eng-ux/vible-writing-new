import { defineConfig } from "@playwright/test";

/**
 * Task 7A Playwright 配置。
 *
 * - globalSetup 先重置独立 E2E 数据库（novel_e2e）；
 * - 后端（uvicorn，端口 8000）与前端（next dev，端口 3000）由 webServer
 *   自动启动；前端经 Next.js 代理访问后端，浏览器不直接访问后端端口；
 * - 所有测试串行（workers=1），避免共享 E2E 库的并发命名冲突。
 */

export default defineConfig({
  testDir: "./tests",
  globalSetup: "./tests/global-setup.ts",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: ".venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
      cwd: "./../backend",
      url: "http://127.0.0.1:8000/ready",
      reuseExistingServer: true,
      timeout: 60_000,
      env: {
        DATABASE_URL: "postgresql+psycopg://postgres:postgres@localhost:5432/novel_e2e",
        ACTOR_ID: "e2e-actor",
        DEPLOYMENT_MODE: "single_user_private",
        API_BIND_SCOPE: "loopback",
        INTERNAL_API_BASE_URL: "http://127.0.0.1:8000",
        APP_ENV: "development",
      },
    },
    {
      command: "npm run dev -- --port 3000",
      cwd: ".",
      url: "http://127.0.0.1:3000/api/ready",
      reuseExistingServer: true,
      timeout: 180_000,
    },
  ],
});

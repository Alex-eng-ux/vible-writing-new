import { execSync } from "node:child_process";
import path from "node:path";

/**
 * Playwright globalSetup：重置独立 E2E 数据库（novel_e2e）。
 *
 * 调用后端 `app.db.e2e_bootstrap`（复用 backend venv 的 Python），创建库 +
 * 按模型建表，保证每个测试运行从干净库开始。
 */
export default function globalSetup(): void {
  const backend = path.resolve(__dirname, "..", "..", "backend");
  execSync(".venv\\Scripts\\python.exe -m app.db.e2e_bootstrap", {
    cwd: backend,
    stdio: "inherit",
    env: {
      ...process.env,
      E2E_ADMIN_URL: "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
      E2E_DATABASE_URL: "postgresql+psycopg://postgres:postgres@localhost:5432/novel_e2e",
    },
  });
}

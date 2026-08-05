# Resource Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为项目树和版本历史增加右键删除入口、确认交互及事务化后端删除接口。

**Architecture:** 后端在现有资源路由中增加 DELETE 接口，按资源类型执行显式删除并复用现有错误封装；前端在 `page.tsx` 中维护统一上下文菜单状态，调用 `api.ts` 的删除函数并局部刷新树或历史。

**Tech Stack:** FastAPI、SQLAlchemy、PostgreSQL、Next.js、React、Playwright、pytest。

## Global Constraints

- 所有删除动作必须携带 `Idempotency-Key`。
- 删除操作必须二次确认，失败时不得清除前端当前状态。
- 中文注释说明删除事务边界、引用保护和副作用；标识符保持英文。
- 不修改无关资源和测试数据。

### Task 1: Backend Delete Contract

**Files:**
- Modify: `backend/app/api/projects.py`, `backend/app/api/volumes.py`, `backend/app/api/chapters.py`, `backend/app/api/scenes.py`
- Modify: `backend/app/api/schemas.py`
- Test: `backend/tests/test_resource_delete.py`

- [ ] 写失败测试：DELETE 项目级联删除后代；DELETE 场景版本对被引用版本返回保护错误。
- [ ] 运行 `pytest backend/tests/test_resource_delete.py -q`，确认接口尚不存在时失败。
- [ ] 增加删除路由和事务化服务逻辑，使用 `Idempotency-Key`，返回 `204` 或统一资源删除响应。
- [ ] 再运行目标测试并确认通过。

### Task 2: Frontend API and Context Menu

**Files:**
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/app/page.tsx`
- Modify: `frontend/src/app/globals.css`

- [ ] 为项目、卷、章节、场景、版本增加 DELETE client 函数。
- [ ] 增加统一右键菜单、确认对话框、删除中状态和错误提示。
- [ ] 删除成功后局部刷新，当前选中资源被删除时清空编辑区。

### Task 3: End-to-End Verification

**Files:**
- Modify: `frontend/tests/editor.spec.ts`

- [ ] 增加右键删除项目或场景的 Playwright 测试，确认菜单、确认框和列表刷新。
- [ ] 运行后端测试、前端类型检查和目标 Playwright 测试。
- [ ] 重启服务并通过浏览器入口验证。

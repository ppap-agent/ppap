# Product API

PPAP 唯一公开 HTTP API（含同进程的 `/mcp`）。isolated-dev 先设置 `PPAP_BUILD_SHA=${PPAP_BUILD_SHA:-local}` 并执行 `docker buildx bake`，再用 `docker compose -f deploy/isolated-dev/compose.yaml up --no-build -d --wait` 启动。

- 路由与 JSON 形状：FastAPI OpenAPI（`app.openapi()`，Web 生成物 `apps/web/src/generated/openapi.ts`），不在此列举。
- 职责：身份/会话、租户与对象授权、文件版本、任务/草稿受理与幂等、结果与历史读取、人工复核、用量与飞书适配。见 [ARCHITECTURE](../../docs/ARCHITECTURE.md#所有权)。
- 不负责：OCR、LibreOffice 转换、长模型/审核执行和 Worker 租约，这些在 Worker。
- 生产 JSON 日志带 `request_id` / `tenant_id` / `round_id`，响应带 `X-Request-ID`；不记录 token 或客户内容。
- MCP 服务合同见 [MCP](../../docs/MCP.md)；测试见 [tests/README](../../tests/README.md)。

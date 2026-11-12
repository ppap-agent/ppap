# Engine Worker

PPAP 的异步执行进程。

isolated-dev 先设置 `PPAP_BUILD_SHA=${PPAP_BUILD_SHA:-local}` 并执行 `docker buildx bake`，再用 `docker compose -f deploy/isolated-dev/compose.yaml up --no-build -d --wait` 启动。Worker 是独立进程；停止 Worker 不应让 API `/health` 失败。

## 当前 main

当前 Worker 已有 heartbeat、`engine.jobs` claim（含过期 `leased` 重试，同一 round 只增加 attempt）、单一执行边界（只执行 job 中冻结的 `ppap-checker-core` 与 Suite 快照，经 bounded Controller protocol），以及产品轮次 findings 写回。审核没有模式（#322）；Worker 不持有 Docker socket，失败不回退其他执行器。Controller 是独立进程 `python -m agent`。claim/complete 的 JSON 日志带 `job_id` / `attempt` / `round_id` / `tenant_id`，不记录 findings 正文。飞书入口仍由 #11 拥有。

## 最终责任

- PostgreSQL job claim / lease / retry
- document preparation
- Excel / PDF / DOCX / DOC processing
- OCR / Vision 等当前审核需要的处理
- audit rule execution
- PASS / NG / ERR findings 与 source locator
- 结果写回产品轮次

Worker 不负责：

- public user HTTP routes
- login/session management
- product navigation
- tenant/organization administration
- 第二套审核台账

## Engine packages

Worker 保持薄层。可复用审核逻辑放在 `packages`，供 Worker、regression tests 和必要的离线运行方式共同使用，不再包装成隐藏 HTTP 服务。

继续支持 legacy `.doc` 时，只保留当前测试和客户路径需要的受限转换边界。

完整 engine migration 与 parity 验收见 #3。

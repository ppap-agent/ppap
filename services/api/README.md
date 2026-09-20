# Product API

PPAP 唯一公开 HTTP API。isolated-dev 使用 `docker compose -f deploy/isolated-dev/compose.yaml up --build -d --wait` 启动；重文档处理和长模型调用不在请求线程执行。

## 当前 main 已实现

- `/health`、`/ready`、`/version`、`/public-config`
- 生产 JSON 日志：`request_id` / `tenant_id` / `round_id`，响应 `X-Request-ID`；不记录 token 或客户内容
- 飞书登录/回调、docs-addon 宿主登录 `POST /login/feishu/host`、`/me`、`/logout`
- 飞书 Drive/Docs 导入：`POST /integrations/feishu/files/import`（session 用户 token → meta → Drive 下载或云文档导出 → 普通 `file_version`）
- 飞书文档关联文件：`GET /integrations/feishu/docs/links?file_token=`（扫描当前云文档 mention/超链接/附件，返回 token/type，不另建飞书文件台账）
- 飞书提交与 PC 共用文件导入 → `POST /drafts` → `PUT /drafts/{id}` → `POST /drafts/{id}/accept`。草稿输入为 `items: [{file_version_id, role, rule_version_id}]`，每个 target 必须选规则，reference 不附规则；旧 `/integrations/feishu/audits` 与整批单规则请求已移除。受理/显式重审按每个 target 500 点记录飞书 usage_events，reference 不计入；不把本地 job 完成当成已扣费。
- 飞书用量：`GET /integrations/feishu/usage/quota`（Partner AI 功能/额度 + 本地已入账试用点数）；`POST /integrations/feishu/usage/events`（记录试用入账或待外部确认的用量）；`POST /integrations/feishu/usage/events/{id}/report`（试用跳过上报；pending 事件经 credential/apply + usage_records/report 后才标 `confirmed`，不把本地 job 完成当成已扣费）
- 飞书写回：`POST /integrations/feishu/rounds/{round_id}/writeback`（把普通 round 的 PASS/NG 履历插回源文档并附 Web 回访链接；ERR 不写回；不另建飞书履历台账）
- 对象授权读取 `/resources/{id}`
- 文件/版本：创建版本、写入 blob、finalize、读取 metadata、download
- isolated-dev fixture session 与 staging cleanup
- 审核草稿、规则选择、受理入队、轮次读取、人工复核、显式重审
- 审核历史：`GET /rounds`（当前主体可读取的 round 摘要，供 docs-addon / Web 回访，不另建飞书履历台账）。客户入口路径是 `/audit-files`。
- 用量读取：`GET /usage`（当前主体在本租户下的 `usage_events`，含 `created_at` 与外部确认状态；不是飞书专属入口，也不发明本地余额）

文件历史由 `file_version_id` 和其存储对象引用确定。当前 finalize 会计算 SHA-256 checksum 作为辅助完整性/比较信息；checksum 不是产品版本 identity。

## 模块责任边界

随着 #11 实现，Product API 仍将负责：

- product authentication/session
- tenant/user/object authorization
- file metadata/version registration
- audit draft / round acceptance and idempotency
- result/history reads
- human review writes
- usage queries and external adapters
- product-required Feishu endpoints

这些责任不代表当前 main 已全部实现；当前事实以上一节和源码/测试为准。

## 不负责

- OCR / LibreOffice / heavy PDF conversion
- long LLM/audit execution
- Worker lease/retry mechanics

审核受理的最终契约见 #7；飞书产品迁移契约见 #11。

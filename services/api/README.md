# Product API

PPAP 唯一公开 HTTP API。使用 `python tools/stack.py up` 启动 isolated-dev；重文档处理和长模型调用不在请求线程执行。

## 当前主要表面

- `/health`、`/ready`、`/version`、`/public-config`
- 飞书登录/回调、`/me`、`/logout`
- 对象授权读取 `/resources/{id}`
- 文件/版本：创建版本、写入 blob、finalize、读取 metadata、download
- isolated-dev fixture session 与 staging cleanup 仅用于本地开发

文件历史由 `file_version_id` 和其存储对象引用确定。新上传创建新版本，旧版本不会被重定向。当前实现会在 finalize 时计算 SHA-256 checksum，并在下载响应中提供它；这是辅助完整性/比较信息，不是产品版本 identity。

## Owns

- product authentication/session
- tenant/user/object authorization
- file metadata/version registration
- audit-round acceptance and idempotency
- result/history reads
- human review writes
- usage queries and external adapters
- product-required Feishu endpoints

## Does not own

- OCR / LibreOffice / heavy PDF conversion
- long LLM/audit execution
- Worker lease/retry mechanics

受理审核时，产品轮次和初始执行工作在一个 PostgreSQL 事务内创建。任何需要重解析的 recognition 工作都提交给 Worker；请求线程只做必要的轻量校验。

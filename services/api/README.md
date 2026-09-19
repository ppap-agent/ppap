# Product API

PPAP 唯一公开 HTTP API。isolated-dev 使用 `python tools/stack.py up` 启动；重文档处理和长模型调用不在请求线程执行。

## 当前 main 已实现

- `/health`、`/ready`、`/version`、`/public-config`
- 飞书登录/回调、`/me`、`/logout`
- 对象授权读取 `/resources/{id}`
- 文件/版本：创建版本、写入 blob、finalize、读取 metadata、download
- isolated-dev fixture session 与 staging cleanup

文件历史由 `file_version_id` 和其存储对象引用确定。当前 finalize 会计算 SHA-256 checksum 作为辅助完整性/比较信息；checksum 不是产品版本 identity。

## 模块责任边界

随着 #7 / #11 实现，Product API 负责：

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

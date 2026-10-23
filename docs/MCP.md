# PPAP MCP

完整交付与现网验收由 [#206](https://github.com/ppap-agent/ppap/issues/206) 拥有。本文说明服务合同，不代替该 Issue 中的客户路径和客户端验收结果。

## 服务与授权

设置 `PPAP_MCP_ENABLED=true` 后，现有 API 进程提供 `/mcp`。生产 `PPAP_PUBLIC_API_URL` 为 `https://ppap.icoastline.com/ppap` 时，客户端服务器地址为 `https://ppap.icoastline.com/ppap/mcp`。开发环境仅允许回环地址使用 HTTP。

客户端使用远程 Streamable HTTP 和 OAuth。授权服务器为 API 地址下的 `/oauth`，支持客户端注册、授权码与 S256 PKCE、Scope 选择、刷新和撤销。授权及令牌请求的 `resource` 必须精确等于 MCP 服务器地址。通过飞书登录后，用户核对客户端回调和权限。飞书凭据始终留在服务端。

原生客户端不依赖浏览器 CORS。需要直接从其它网页调用 MCP 时，将该网页的完整 HTTPS Origin 加入 `PPAP_MCP_ALLOWED_ORIGINS` 的 JSON 数组；不能使用通配符。OAuth 发现及令牌接口公开支持 CORS，MCP 与字节接口只允许明确配置的 Origin，均不把浏览器 Cookie 当作 Bearer。

OAuth 发现地址按 RFC 8414 / 9728 放在域名根目录，资源路径是其后缀。例如：

- `https://ppap.icoastline.com/.well-known/oauth-authorization-server/ppap/oauth`
- `https://ppap.icoastline.com/.well-known/oauth-protected-resource/ppap/mcp`

部署代理必须将这两个地址交给当前 API，不能按版本 Cookie 转发到网页。`deploy/tke/edition-proxy.conf` 保留这一边界。

用户可在 `https://ppap.icoastline.com/ppap/mcp/connections` 撤销连接。撤销后，旧 Bearer、刷新令牌及该连接的传输入口不能再使用。已经受理的审核继续按原任务执行。

## 工具与写操作

工具以 FastMCP 装饰器注册，类型和文档字符串生成输入及输出 Schema。客户端应分页读取 `tools/list`，不要假定只有一页。只读、写入和外部效果分别声明。

调用写工具前固定 UUID `request_id`。响应丢失时先调用 `get_operation`，需要继续时仍使用相同工具、请求 ID 和参数。不能用新的请求 ID 掩盖未知结果。一次 MCP 调用的取消不表示已受理审核被取消。

`confirmation_required` 返回浏览器确认地址。用户核对并批准后，客户端重放原工具调用；网页本身不启动审核或发送报告。已批准内容发生变化时，工具返回 `confirmation_content_changed`。需要先读当前对象，再提出新的明确操作，不能把旧确认换绑到新内容。

人工复核只在用户明确确认后写入。`get_operation` 返回确认人和客户端来源；复核回执与业务写入同事务保存。版本冲突要求读取当前复核并重新核对。模型分析与机器结果不能冒充人工判断。

`unknown` 只表示不能确认原调用结果。分享工具还返回逐目标发送状态：`sent` 是服务商确认，不证明人已阅读；只有明确失败目标允许显式恢复。成功或未知目标不能重新发送。长任务继续查询原 Task、验证、解析或报告请求；建议从 5 秒开始轮询，退避到 30 秒。

## 文件与报告字节

本地上传顺序：

1. 计算原文件 SHA-256，调用 `create_file` 或 `create_file_version`，固定请求 ID 和字节数。
2. 调用 `create_upload_transfer`，取得 15 分钟有效的 PUT 地址。
3. 客户端通过 HTTPS 发送原始字节，并携带同一 OAuth 委托的 Bearer。单文件上限 200 MiB。
4. 调用 `finalize_file` 固化版本，再调用 `validate_file`。
5. 断线后读取 `get_upload` 和 `get_operation`，核对原版本及摘要，再继续原操作。

`download_file`、`download_round_file`、`download_report` 和 ZIP 下载工具返回同样需要 Bearer 的 GET 地址。报告入口绑定具体结果尝试和 artifact，不自动替换为最新产物。传输 URL 的查询参数不能改变所绑定的产物。

远程 MCP 服务不能读取客户端机器的路径。目标客户端必须具有可使用其 OAuth 凭据的字节传输能力；仅能调用 JSON 工具的客户端还需要传输适配，或选择 URL/飞书导入。不能因为服务器提供上传工具就宣称所有客户端的本地文件体验已完成。

## Resources 与 Prompts

Resources 包括固定文件版本、规则版本、轮次结果、报告包和已分享报告。每次读取都重新授权，不把 URI 当作权限。模板支持 `pointer`、`offset`、`limit` 查询参数；`pointer` 是 JSON Pointer，`limit` 上限为 100。返回正文上限为 96 KiB，过大时应选择具体字段或缩小分页，不能截断并声称结果完整。

例如 `ppap://rounds/<round_id>?pointer=%2Fread_view%2Ffiles&offset=0&limit=10` 读取已授权轮次的一页文件事实。大文件和 PDF 使用字节入口。

`summarize_round` 和 `compare_rounds` Prompts 指导客户端读取原事实、引用固定版本、区分机器结果、人工复核与模型解释。它们不会自动写入业务记录。原件内容是数据，不能授权新工具操作；未知、空结果或执行错误不能变成 PASS。

## 部署、回滚与证据

先应用 `0034_mcp.sql`，再部署使用同一锁文件构建的 API。迁移保留现有业务表和 Worker 状态；MCP 授权表对 Worker 不可读。开启功能前验证域名根目录发现、代理 Host/HTTPS、授权码回调、实际 Bearer 校验和字节传输。MCP JSON 请求上限 1 MiB；文件入口继续保留 200 MiB。

回滚时先关闭 `PPAP_MCP_ENABLED`，再按现有部署流程回滚 API。保留授权和操作回执表，不回退业务轮次、不删除未知发送记录，也不因回滚重新提交原请求。恢复服务后仍从原回执继续。

`tests/integration/product/test_mcp.py` 使用真实 PostgreSQL、API 角色和 MCP/OAuth 协议。对象存储与身份为隔离环境边界；识别用例控制外部模型元数据。因此，这些测试不证明真实飞书登录、模型质量、真实发送或任一桌面客户端已经验收。Codex、WorkBuddy 和豆包必须分别记录实际客户端版本、连接、续期、文件操作及业务路径结果。

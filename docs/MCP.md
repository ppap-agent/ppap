# PPAP MCP

完整交付与现网验收由 [#206](https://github.com/ppap-agent/ppap/issues/206) 拥有。本文说明服务合同，不代替该 Issue 中的客户路径和客户端验收结果。

## 服务与授权

设置 `PPAP_MCP_ENABLED=true` 后，现有 API 进程提供 `/mcp`。生产 `PPAP_PUBLIC_API_URL` 为 `https://ppap.icoastline.com/ppap` 时，客户端服务器地址为 `https://ppap.icoastline.com/ppap/mcp`。开发环境仅允许回环地址使用 HTTP。

客户端使用远程 Streamable HTTP 和 OAuth。授权服务器为 API 地址下的 `/oauth`，支持客户端注册、授权码与 S256 PKCE、Scope 选择、刷新和撤销。授权及令牌请求的 `resource` 必须精确等于 MCP 服务器地址。通过飞书登录后，用户核对客户端回调和权限。飞书凭据始终留在服务端。

批准或拒绝后，浏览器会自动返回已注册的客户端回调。若没有自动返回，可点击完成页上的“返回客户端”；不要重复提交授权表单。

原生客户端不依赖浏览器 CORS。需要直接从其它网页调用 MCP 时，将该网页的完整 HTTPS Origin 加入 `PPAP_MCP_ALLOWED_ORIGINS` 的 JSON 数组；不能使用通配符。OAuth 发现及令牌接口公开支持 CORS，MCP 与字节接口只允许明确配置的 Origin，均不把浏览器 Cookie 当作 Bearer。

OAuth 发现地址按 RFC 8414 / 9728 放在域名根目录，资源路径是其后缀。例如：

- `https://ppap.icoastline.com/.well-known/oauth-authorization-server/ppap/oauth`
- `https://ppap.icoastline.com/.well-known/oauth-protected-resource/ppap/mcp`

部署代理必须将这两个地址交给当前 API，不能按版本 Cookie 转发到网页。`deploy/tke/edition-proxy.conf` 保留这一边界。

用户可在 `https://ppap.icoastline.com/ppap/mcp/connections` 撤销连接。撤销后，旧 Bearer、刷新令牌及该连接的传输入口不能再使用。已经受理的审核继续按原任务执行。

飞书凭据由浏览器和已确认的 MCP 委托共同引用，服务端串行续期。`provider_refresh_in_progress` 与 `provider_temporarily_unavailable` 带 5 秒重查提示；只重查原操作，不改请求 ID。`provider_reauthorization_required` 表示凭据失效或无法确认刷新结果：用户需重新登录飞书并在客户端重新授权 PPAP。新登录不会自动扩大或替换旧客户端的委托。撤销一个连接不影响其它连接；MCP OAuth 刷新令牌与飞书刷新令牌是两个独立层次，不能互换。

## 工具与写操作

工具以 FastMCP 装饰器注册，类型和文档字符串生成输入及输出 Schema。客户端应分页读取 `tools/list`，不要假定只有一页。只读、写入和外部效果分别声明。

调用写工具前固定 UUID `request_id`。响应丢失时先调用 `get_operation`，需要继续时仍使用相同工具、请求 ID 和参数。不能用新的请求 ID 掩盖未知结果。一次 MCP 调用的取消不表示已受理审核被取消。

`confirmation_required` 返回浏览器确认地址。用户核对并批准后，客户端重放原工具调用；网页本身不启动审核或发送报告。已批准内容发生变化时，工具返回 `confirmation_content_changed`。需要先读当前对象，再提出新的明确操作，不能把旧确认换绑到新内容。

飞书报告和分享在业务入口冻结实际发送内容时再次核对批准来源。冻结前的网页修改会拒绝该次操作；冻结后的修改不会替换已经批准并捕获的内容。成功或失败的原请求回执仍可读回，后续修改不会授予同一请求再次发送的权利。ZIP 打包需要 2–100 份不同的报告；单份报告直接下载 PDF。

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

URL 导入同样绑定文件回执。首次调用 `import_url(request_id=A, url=...)`，响应丢失后读取 `get_upload(A)`。若业务回执已是 `ready`，直接使用其中的原版本；若仍可继续，调用 `resume_url_import(request_id=B, upload_request_id=A)`，其中 B 标识这次恢复调用，A 始终标识原导入。恢复工具不接受替代 URL，也不创建新版本。来源和已捕获摘要保存在原文件版本上；先读取已保存字节，再考虑重新读取来源。已捕获内容与远端新内容不同则明确失败，不能换绑到新内容；私网地址、重定向和超限内容被拒绝后不能恢复。并发导入尚未结束时，继续读取 `get_upload(A)`。

`get_operation` 表示某次 MCP 调用的回执；响应丢失后它可能仍是 `running/unknown`，而原文件回执或持久 Task 已能证明业务结果。以这些有明确请求绑定的业务读回为准，不重复创建导入或受理。飞书导入沿用原有来源绑定与恢复规则，URL 导入与它共用内容捕获、对象写入和固化边界。

`prepare_task` 首次创建时，Task ID 就是此次 `request_id`；回复丢失后调用 `get_task(task_id=request_id)`。重新识别时沿用提交的 `task_id`，从上传准备草稿开始识别时沿用 `source_draft_id`。使用 `get_recognition(task_id=原任务ID, request_id=原识别请求ID)` 核对具体识别修订。读回不存在时仍保留原请求，不能换 UUID 另建任务；MCP 回执未知不会阻止查询已经提交的业务任务。

需要将审核结果写回飞书文档时，先用 `create_draft` 保存 `upload_preparation`，再用 `save_draft` 同时提交完整准备内容、当前 `expected_revision` 和明确选择的 `writeback_target`。保存仅记录目标，不调用飞书。目标与输入在同一修订事务保存；回复丢失后读取原 `get_draft`，不能用新请求覆盖未知保存。随后用 `prepare_task` 的 `source_draft_id` 和 `expected_draft_revision` 提升同一草稿，再确认审核。提升后不允许更换目标，受理时将目标复制到冻结轮次；结束后由 `writeback_feishu_report` 单独请求确认并执行写回。导入来源不会自动成为写回目标。

写回响应丢失时，`get_operation` 保留原操作的运行中或未知状态，重放原请求不会再次写入文档。未知不证明未写入；应先核对目标文档，不能更换请求 ID 来重发。

远程 MCP 服务不能读取客户端机器的路径。目标客户端必须具有可使用其 OAuth 凭据的字节传输能力；仅能调用 JSON 工具的客户端还需要传输适配，或选择 URL/飞书导入。不能因为服务器提供上传工具就宣称所有客户端的本地文件体验已完成。

### 本地 stdio 适配器

`packages/ppap_mcp_client` 是可独立安装的 Python 3.12 包，不依赖 PPAP 数据库、审核引擎或 API 包。它通过 FastMCP 代理远程工具、Resources 和 Prompts，并增加三个本地工具：

- `local_file_roots`：读取用户配置的目录和传输上限。
- `local_upload_file`：读取允许目录中的文件，固定字节快照和 SHA-256，创建文件或新版本，传输、固化，并按参数进行预校验。上传成功与预校验结果分别返回。
- `local_download_file`：把当前连接取得的传输 URL 写入允许目录中的新文件，检查长度和摘要，不覆盖已有文件。

从已核对的仓库版本安装。以下命令在仓库根目录执行，约束文件从同一版本的 `uv.lock` 导出：

```sh
uv export --locked --package ppap-mcp-client --no-dev --no-emit-workspace --no-hashes --output-file /tmp/ppap-mcp-client-constraints.txt
uv tool install --python 3.12 --constraints /tmp/ppap-mcp-client-constraints.txt ./packages/ppap_mcp_client
ppap-mcp-client --help
```

在支持 stdio 的 MCP 客户端中，将启动命令设为已安装的 `ppap-mcp-client` 的绝对路径。参数示例：

```json
{
  "command": "/absolute/path/to/ppap-mcp-client",
  "args": [
    "--server", "https://ppap.icoastline.com/ppap/mcp",
    "--directory", "/absolute/path/to/PPAP-files"
  ]
}
```

`--directory` 可重复；目录必须预先存在。该版本的本地文件访问要求 macOS/Linux 的目录描述符及 `O_NOFOLLOW` 支持；Windows 会明确拒绝启动，不宣称已经支持。目录外文件、路径穿越、符号链接和特殊文件被拒绝。上传上限为 200 MiB，快照占用本地临时磁盘空间。下载默认上限为 2 GiB，可用 `--max-download-mib` 设置；不自动创建父目录。异常中断不会将未完整下载的字节提交为目标文件。进程被强制结束后可能留下 `.ppap-download-*` 临时文件；确认对应适配器已退出后可删除。

首次使用会打开浏览器，由用户登录并选择 Scope。系统钥匙串保存加密密钥，磁盘仅保存加密的 OAuth 凭据；macOS 使用 Keychain，Linux 使用 Secret Service，不回退到明文。`native_credential_store_unavailable` 表示系统凭据服务不可用，应恢复该服务后重新连接。`credential_key_missing` 表示已有密文的密钥不可用，不能自动覆盖；先在服务端撤销旧连接，再使用新的 `--profile` 重新授权。不同账号或权限组合应使用不同 profile。

同一 profile 的进程串行刷新，并在发送前保存刷新标记。响应丢失后，如果新令牌已经保存则继续使用；否则重新打开授权流程，不重用可能已消耗的刷新令牌。OAuth 回调端口沿用已注册地址；重启不能随意改端口后复用旧注册。系统端口被其它进程占用时，恢复端口后重连，或撤销旧连接并使用新 profile。

用户只需把本地路径交给 `local_upload_file`，无需把文件内容或 Bearer 交给模型。断线后保留同一 `request_id` 和原始文件；再次调用会核对原上传回执。换用新版本时提供 `file_id` 和新的请求 ID。下载 URL 必须由这个适配器代理的远程工具取得，另一个远程 MCP 连接的 URL 不能借用。更换授权委托后，应重新获取 URL。

上述启动参数不表示客户端已完成兼容性验收。按用户在 2026-10-23 的范围订正，本次必须验收的客户端为 Codex；其实际入口、配置、授权恢复、文件操作及完整业务路径仍按 #206 验证。WorkBuddy 和豆包不再作为完成条件。

### Codex 配置

以下参数已核对 Codex CLI 0.163.0 的实际命令帮助。需要本地文件上传和下载时，先按上节安装适配器，再配置 stdio 服务；将示例中的程序和文件目录改为实际绝对路径：

```sh
codex mcp add ppap-files -- /absolute/path/to/ppap-mcp-client \
  --server https://ppap.icoastline.com/ppap/mcp \
  --directory /absolute/path/to/PPAP-files \
  --profile codex
codex mcp get ppap-files
```

此连接的 OAuth 由适配器管理，首次连接时打开授权页；不对 stdio 服务执行 `codex mcp login`。在 Codex 中先调用 `get_identity` 核对用户和租户，再调用 `local_file_roots` 取得允许目录的规范路径，之后使用该目录内的绝对路径上传。`mcp get` 只显示配置；成功调用工具才证明已连接。发布、受理、人工复核和发送仍须完成对应网页确认。

只使用远程业务工具、URL 或飞书导入时，也可直接配置原生 HTTP 连接：

```sh
codex mcp add ppap --url https://ppap.icoastline.com/ppap/mcp
codex mcp login ppap --oauth-client-registration dcr
```

如果添加服务时已完成登录，无须重复执行登录命令。原生 HTTP 连接不会获得适配器的三个本地文件工具。两种连接各自取得委托，不共享传输 URL；通常选择符合文件输入方式的一种即可。授权页只勾选此次工作需要的范围。

停止使用时，先在 PPAP 连接管理页撤销对应委托。原生 HTTP 连接随后可执行 `codex mcp logout ppap` 清除 Codex 保存的凭据；适配器的凭据由其独立 profile 管理。仅退出客户端或删除配置不会撤销服务端委托。续期或访问失败时，先读取错误与原业务回执，按上节恢复步骤处理，不改变原请求 ID。

## Resources 与 Prompts

Resources 包括固定文件版本、规则版本、轮次结果、报告包和已分享报告。每次读取都重新授权，不把 URI 当作权限。模板支持 `pointer`、`offset`、`limit` 查询参数；`pointer` 是 JSON Pointer，`limit` 上限为 100。返回正文上限为 96 KiB，过大时应选择具体字段或缩小分页，不能截断并声称结果完整。

例如 `ppap://rounds/<round_id>?pointer=%2Fread_view%2Ffiles&offset=0&limit=10` 读取已授权轮次的一页文件事实。大文件和 PDF 使用字节入口。

`summarize_round` 和 `compare_rounds` Prompts 指导客户端读取原事实、引用固定版本、区分机器结果、人工复核与模型解释。它们不会自动写入业务记录。原件内容是数据，不能授权新工具操作；未知、空结果或执行错误不能变成 PASS。

## 部署、回滚与证据

先停止旧 API 的请求及后台凭据消费者，应用 `0034_mcp.sql` 与 `0035_provider_authorizations.sql`，再启动使用同一锁文件构建的新 API。此次凭据迁移不能让新旧 API 并存：旧版会直接刷新会话内的令牌。迁移将身份唯一的旧浏览器会话绑定到共享凭据；身份不明确、旧 MCP 凭据无法精确匹配或刷新状态未知时要求重新授权。旧会话与 MCP 表内的凭据副本会被清空。迁移保留业务表和 Worker 状态；MCP 与飞书授权表对 Worker 不可读。开启功能前验证域名根目录发现、代理 Host/HTTPS、授权码回调、实际 Bearer 校验和字节传输。MCP JSON 请求上限 1 MiB；文件入口继续保留 200 MiB。

回滚时先关闭 `PPAP_MCP_ENABLED`。若目标 API 仍支持共享飞书凭据，可按现有流程回滚；若回到 `0035` 之前的代码，先停止新 API，并以迁移角色撤销 `provider_authorization_id IS NOT NULL` 的浏览器会话与 MCP 委托，再启动旧 API，要求用户重新登录。不得把共享凭据复制回旧会话表，否则会恢复多处刷新竞争。保留授权和操作回执表，不回退业务轮次、不删除未知发送记录，也不因回滚重新提交原请求。重新升级后可恢复仍有效的连接；被撤销的连接须重新授权，原业务请求仍按持久业务回执核对。

`tests/integration/product/test_mcp.py` 使用真实 PostgreSQL、API 角色和 MCP/OAuth 协议。对象存储与身份为隔离环境边界；识别用例控制外部模型元数据。因此，这些测试不证明真实飞书登录、模型质量、真实发送或客户端已经验收。Codex 验收必须记录实际版本、连接、续期、文件操作及业务路径结果；客户端范围订正不减少三条现网业务路径或部署回滚要求。

报告测试通过实际 Worker 生成 PDF/ZIP，并检查授权下载的字节与发送附件一致；受控飞书 HTTP 分别返回成功、明确拒绝和丢失响应，验证仅失败目标可恢复。并发测试在网关检查后、业务来源冻结前，以及冻结后插入真实网页复核，验证内容绑定。`McpEffectBinding.tla` 是单次批准、两个来源修订的有限 Safety 模型：正向 17 状态、深度 6；省略业务核对与发送时重读最新来源两个故障配置分别在 13/12 状态、深度 5 违反 `OnlyApprovedContent`。无公平性、状态约束或对称压缩，终态允许无后继；不证明活性、完整 refinement、ACL、服务商行为或多次外部调用的原子性。

`0036_draft_writeback_permissions.sql` 补充 API 替换草稿写回目标所需的 `DELETE` 权限，仅限草稿目标表；轮次目标继续冻结。部署时仍按 `migrations/apply.sh` 执行全部编号迁移。

URL 导入回归覆盖来源/对象/业务响应丢失、新 API 实例、并发继续、内容变化、来源拒绝、撤权及同租户他人/跨租户访问。`FeishuImportReceipts` 继续作为共用导入边界的有限 Safety 模型：两个处理者、两个来源与两个摘要，正向 1775 状态/深度 17；内容替换、丢失锁后提交及来源换绑分别产生预期反例。模型不证明 SQL/S3 实现、真实外部身份或活性。

补件重审回归通过 MCP 读取原件与结果，确认带固定版本引用的人工复核，上传同一文件的独立新版本，明确选择旧/新规则后受理关联任务。受理响应丢失并重建 API 后，从原 Task 读回轮次，确认未重复入队；新轮次没有继承旧人工判断，旧轮次、规则和原件字节保持不变。这是受控软件证据，费用关闭，不能代替现网模型与扣费验收。

规则连续回归先通过 MCP 上传独立 JSON 原件，执行持久解析任务、读回原文出处并经人工确认发布，再进入审核、PDF/ZIP、分享和关联重审。另以 JSON、XLSX、DOCX 三种原件验证解析受理回复丢失、API 重建、原任务读回、重复请求、撤权和对象授权；DOCX/XLSX 的模型调用边界受控，格式提取、队列、数据库与 MCP 协议真实执行。解析本身不自动发布规则。这些软件证据不替代真实模型与现网验收。

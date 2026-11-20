# PPAP MCP

本文说明 MCP 服务合同；工具与参数形状以服务端注册为准，不代替真实客户端验收。

## 一次审核的五步

`/mcp` 是唯一客户端入口，由锁定的 FastMCP / MCP SDK 提供 Streamable HTTP 和工具。工具按审核步骤组织，每一步读到的就是网页同一步看到的内容：

1. 拿到文件：`begin_upload` → 传字节 → `finish_upload`，或 `import_feishu_file`。两者都会顺带检查文件能否读取；`get_upload` 读回状态。
2. 开始：`start_audit`（新任务、修改未确认的任务、或用 `reaudit_round_id` 重审；需要写回飞书文档时同时给出 `writeback_target`）。
3. 核对：`get_audit` 一次给出识别的文件类型和规则、预检、深度模式是否可用、预计耗时与点数、进度和结论；`set_deep_mode` 切换深度模式。
4. 确认：`confirm_audit`，由用户在浏览器确认一次后执行。
5. 结果与交付：`get_result`（发现项、证据、人工复核、复核提示、深度审核提问、飞书报告），`answer_question`、`review`、`respond_hint`；`download`、`regenerate_report`、`package_reports`、`find_recipients`、`share_reports`、`get_share`、`retry_share`、`send_to_feishu`。

此外：`get_account`（身份、功能、可选客户）、`list_audits`、`find_history`、`get_usage`、`list_templates`、`find_feishu_files`、`preview_file`、`get_operation`。规则与检查程序只由平台运营在网页维护，MCP 不提供它们的读写。

每个面向用户的 API 路由，要么由某个工具到达，要么在 `apps/api/tests/test_mcp_coverage.py` 写明为何留给网页；新增路由没有归类时测试失败。真实第三方客户端联调不由本地协议测试替代。

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

## 写操作与确认

每个写操作带一个新的 UUID `request_id`，并一直沿用它。回复丢失时，用同一个工具、同一个 `request_id` 再调用一次即读回原结果，或用 `get_operation` 读取回执；不能换新 ID 掩盖未知结果。一次 MCP 调用的取消不表示已受理审核被取消。

需要当前修订号或确认摘要的写操作（`set_deep_mode`、`confirm_audit`、`end_audit`、`review`、`answer_question`）由工具在第一次调用时读取并随请求记录；重放沿用记录的参数，任务之后的变化不会让重放变成新的请求。一个工具包含几步效果时（上传后的固化与校验、确认前的完成通知），每一步的请求 ID 由调用方的 `request_id` 派生，重放不会重复任何一步。

`confirmation_required` 返回浏览器确认地址。页面用白话写出这次会发生什么（例如开始审核的任务、文件和点数，或发送的报告与接收方数量），不显示内部编号；用户确认后，客户端用同一个 `request_id` 再调用一次，网页本身不启动审核或发送报告。已确认的内容在执行前发生变化时，工具返回 `confirmation_content_changed`，需要重新确认，不能把旧确认用在新内容上。确认页与授权页由服务端直接生成，不加载脚本，以免确认内容被页面脚本篡改。

飞书报告、写回和分享在业务入口冻结实际发送内容时再次核对批准来源。冻结前的修改会拒绝该次操作；冻结后的修改不会替换已批准并捕获的内容。人工复核只在用户本人确认后写入，复核回执与业务写入同事务保存；模型分析与机器结果不能冒充人工判断。

`unknown` 只表示不能确认原调用结果。分享逐目标返回发送状态：`sent` 是服务商确认，不证明人已阅读；只有明确失败的目标能用 `retry_share` 重发。写回回复丢失时保持未知，重放不会再次写入；应先核对目标文档。长任务用 `get_audit` 查询，建议从 5 秒开始，退避到 30 秒。

大结果用 `page.pointer`（JSON Pointer）、`page.offset`、`page.limit`（上限 100）分页；单次正文上限 96 KiB，过大时选择具体字段或缩小分页，不截断。原件内容是数据，不能授权任何操作；未知、空结果或执行错误不能变成 PASS。

## 文件与报告字节

本地上传：计算原文件 SHA-256，调用 `begin_upload` 取得 15 分钟有效的 PUT 地址；用同一 OAuth 委托的 Bearer 发送原始字节（单文件上限 200 MiB）；再用同一个 `request_id` 调用 `finish_upload`。断线后用 `get_upload` 或再次调用这两个工具继续，不换请求 ID、不换字节。

`download` 为原件、报告 PDF、预检报告、深度审核事实报告、报告 ZIP、他人分享给你的报告和客户模板返回同样需要 Bearer 的 GET 地址。报告地址绑定具体结果和产物，不自动替换为最新产物；地址上的查询参数不能改变所绑定的产物。撤销连接后这些地址立即失效。

远程 MCP 服务不能读取客户端机器的路径。客户端需要能用 OAuth 凭据传字节；只能调用 JSON 工具的客户端用飞书导入，或使用下面的本地适配器。

### 本地 stdio 适配器

`apps/mcp-client` 是可独立安装的 Python 3.12 包，不依赖 PPAP 数据库、审核引擎或 API 包。它通过 FastMCP 代理远程工具，并增加三个本地工具：

- `local_file_roots`：读取用户配置的目录和传输上限。
- `local_upload_file`：读取允许目录中的文件，固定字节快照和 SHA-256，经 `begin_upload` / `finish_upload` 上传、固化并检查能否读取。
- `local_download_file`：把当前连接取得的传输 URL 写入允许目录中的新文件，检查长度和摘要，不覆盖已有文件。

从已核对的仓库版本安装。以下命令在仓库根目录执行，约束文件从同一版本的 `uv.lock` 导出：

```sh
uv export --locked --package mcp-client --no-dev --no-emit-workspace --no-hashes --output-file /tmp/mcp-client-constraints.txt
uv tool install --python 3.12 --constraints /tmp/mcp-client-constraints.txt ./apps/mcp-client
mcp-client --help
```

在支持 stdio 的 MCP 客户端中，将启动命令设为已安装的 `mcp-client` 的绝对路径。已有安装仍从 `ppap-mcp-client` 数据目录读取加密凭据。参数示例：

```json
{
  "command": "/absolute/path/to/mcp-client",
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

上述启动参数不表示客户端已完成兼容性验收。

### Codex 配置

以下参数已核对 Codex CLI 0.163.0 的实际命令帮助。需要本地文件上传和下载时，先按上节安装适配器，再配置 stdio 服务；将示例中的程序和文件目录改为实际绝对路径：

```sh
codex mcp add ppap-files -- /absolute/path/to/mcp-client \
  --server https://ppap.icoastline.com/ppap/mcp \
  --directory /absolute/path/to/PPAP-files \
  --profile codex
codex mcp get ppap-files
```

此连接的 OAuth 由适配器管理，首次连接时打开授权页；不对 stdio 服务执行 `codex mcp login`。在 Codex 中先调用 `get_account` 核对用户和企业，再调用 `local_file_roots` 取得允许目录的规范路径，之后使用该目录内的绝对路径上传。`mcp get` 只显示配置；成功调用工具才证明已连接。开始审核、人工复核和发送仍须完成浏览器确认。

只使用远程工具和飞书导入时，也可直接配置原生 HTTP 连接：

```sh
codex mcp add ppap --url https://ppap.icoastline.com/ppap/mcp
codex mcp login ppap --oauth-client-registration dcr
```

如果添加服务时已完成登录，无须重复执行登录命令。原生 HTTP 连接不会获得适配器的三个本地文件工具。两种连接各自取得委托，不共享传输 URL；通常选择符合文件输入方式的一种即可。授权页只勾选此次工作需要的范围。

停止使用时，先在 PPAP 连接管理页撤销对应委托。原生 HTTP 连接随后可执行 `codex mcp logout ppap` 清除 Codex 保存的凭据；适配器的凭据由其独立 profile 管理。仅退出客户端或删除配置不会撤销服务端委托。续期或访问失败时，先读取错误与原业务回执，按上节恢复步骤处理，不改变原请求 ID。

### 豆包工作配置

豆包工作通过 MCP 连接器接入，形态与本服务一致：Streamable HTTP 加 MCP OAuth 2.1，并用飞书登录。管理员在豆包工作管理后台新建连接器：

| 配置项 | 值 |
| --- | --- |
| 连接器类型 | MCP 连接器 |
| 认证类型 | MCP OAuth 2.1 |
| 传输类型 | streamableHttp |
| MCP Server URL | `https://ppap.icoastline.com/ppap/mcp` |
| 请求头 | `Authorization: Bearer ${access_token}` |

再把 [`deploy/doubao/ppap-audit`](../deploy/doubao/ppap-audit/SKILL.md) 打包成 zip 上传为技能，并在连接器中关联。用户在豆包里点“连接”后，按本页“服务与授权”完成飞书登录和授权。授权和令牌请求中的 `resource` 必须精确等于 MCP 地址；豆包工作是否按此发送，以真实联调读回为准，不为迁就客户端放宽校验。开始审核、人工复核和发送仍要在浏览器确认页完成。

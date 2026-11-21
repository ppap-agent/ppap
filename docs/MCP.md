# PPAP MCP

用户给出一个飞书文件夹，PPAP 在服务端把它准备成一次可以确认的审核；遇到只有人能决定的事才问人，决定后自己接着做。本文是这份合同；工具与参数形状以服务端注册为准，真实客户端的联调不由它代替。

## 模型

**审核任务（Task）是中心。** 它是一个持久的状态机：记着资料从哪来、每份文件导入得怎样、用户做过哪些决定。工具只是对它下命令或读它的状态。

```
事件 + 任务状态 → 新状态          product.workflow.apply
任务状态 + 事实 → 要做的事        product.workflow.effects
任务状态 + 事实 → 客户端看到的    product.workflow.project（阶段、问题、下一步）
```

这三个函数不做任何读写。读飞书、导入、排检查、建审核任务这些有副作用的事由 `api.mcp_runtime` 执行，全部经过网页同样使用的 Product API，每一步的请求编号由任务编号推导，所以中途断在哪里，再做一遍都不会重复。任务状态存在 `product.audit_workflows`，按修订号比较后写入；审核任务、轮次、预检、报告仍由各自的拥有者保存，任务只引用它们。

- **文件之间互不依赖，结果累积。** 每份文件要么成为一份导入好的版本，要么成为一个问题。20 份里 2 份失败，得到 18 份结果和 2 个问题，不是第一个错误。
- **阶段之间有依赖，后一个只用前一个的产出。** 读文件夹 → 导入并检查能否读取 → 固定文件版本 → 识别与匹配要求 → 预检 → 报价。一个问题只挡住依赖它的部分。
- **中断是常态。** 进程重启、客户端离开、会话结束、需要人决定，对任务是同一件事：停在某处。任何人任何时候读它，得到同样的问题和下一步。

## 工具

| 工具 | 类型 | 作用 |
| --- | --- | --- |
| `find_submissions` | 读 | 浏览用户的飞书云盘，找到供应商提交的文件夹，以及从它准备过的审核 |
| `prepare_audit` | 写 | 从飞书文件夹（或单个飞书文件）准备一次审核；回复里的 `task_id` 是之后各工具用的任务编号 |
| `get_task` | 读 | 任务的阶段、各步进度、逐文件一行、问题和下一步；读它也让中断的任务继续 |
| `get_issue` | 读 | 一个问题的全部内容和它接受的选项 |
| `resolve_issue` | 写 | 提交用户对一个问题的决定 |
| `confirm_audit` | 写 | 按用户同意的报价开始审核 |
| `get_results` | 读 | 审核结论与检查项，分页 |
| `get_finding` | 读 | 一个检查项的条款、证据与原件位置 |
| `record_review` | 写 | 记录人工复核，机器结论不被覆盖 |
| `publish_report` | 写 | 把审核结果交付为飞书报告文档 |
| `find_audits` | 读 | 用户的全部审核，不论从哪里发起 |
| `get_operation` | 读 | 按 `request_id` 读回一次写操作的回执 |
| `get_account` | 读 | 当前用户、可选客户、剩余点数 |

文件只来自飞书，结果只回到飞书：MCP 不上传、不下载字节，也没有本地适配器。规则与检查程序只由平台运营在网页维护。每个面向用户的 API 路由，要么由这些工具到达，要么在 `apps/api/tests/test_mcp_coverage.py` 写明为何留给网页。

网页或飞书插件发起的审核同样是任务：`get_task`、`get_results`、`record_review`、`publish_report` 对它一样可用，只是没有文件夹可供重新准备。

## 每次返回

```json
{
  "schema_version": "1.0",
  "task_id": "…",
  "task_revision": 8,
  "result": { "phase": "awaiting_decision", "stages": [], "files": {}, "quote": {} },
  "issues": [],
  "next": { "kind": "user_action", "issue_id": "iss_…" },
  "available_actions": []
}
```

`result` 是已经确定的事。`phase` 取 `preparing`、`awaiting_decision`、`ready_to_confirm`、`auditing`、`completed`、`closed`；导入、识别、预检、报价、审核、报告各有自己的状态，不挤进一个枚举，所以“审核结论 NG，同时报告生成失败”可以如实表达。

`next` 是此刻该做的一件事，由服务端从任务状态算出：

| `kind` | 客户端做什么 |
| --- | --- |
| `wait` | 不重复发起；`check_after_ms` 之后再读 `observe_with` 指的工具 |
| `user_action` | 把问题交给用户。带 `issue_id` 的用 `resolve_issue` 提交选择；带 `url` 的请用户在浏览器确认，再按 `then` 用同一个 `request_id` 调用 |
| `call_tool` | 在用户意图允许时调用所指工具；带 `reason` 时表示任务已变化，先重新读 |
| `recover` | 用同一个 `request_id` 重复原调用或读回执，绝不换新编号 |
| `done` | 这一步的目标已达到，如实转述业务结论 |
| `blocked` | 说明原因；`message` 是给用户的话，原样转述 |

`next` 是建议，不是授权。`confirm_audit`、`record_review`、`publish_report` 只在用户明确要求时调用，并且仍要用户本人在浏览器确认。`next` 里没有任何来自被审文件的内容。

## 问题与决定

问题（Issue）是流程里需要处理的事，不是审核结论。它由任务状态推导，起因消失问题即消失；每个问题带着服务端给出的 `allowed_actions`，`resolve_issue` 只接受其中之一，提交时带上决定所依据的 `task_revision`。过期的修订、已关闭的问题、不在列表里的动作一律拒绝并指回 `get_task`。

| 类别 | 例子 | 可选动作 |
| --- | --- | --- |
| `access` | 读不到文件夹 | 再读一次 |
| `import` | 某份文件导入失败、读不出内容；子文件夹层级太深未读取、类型不支持、内容重复（后三者仅告知） | 再导入、不审这份、重新读取文件夹 |
| `source_changed` | 确认前发现文件夹有增删 | 按现在的文件夹重新准备、按原来的资料审核 |
| `rule_conflict` | 没指定客户导致找不到要求；复审时要求有新版本 | 选客户、选版本 |
| `missing_evidence` | 没有一份文件对得上要求；预检发现的问题（不阻止审核） | 补件后重新读取、结束、不审这份 |
| `quote_changed` | 深度模式不可用 | 改用标准模式 |
| `fact_confirmation` | 审核后需要人确认的原件事实 | 回答 |
| `review` | 结论与之前不同，建议复核 | 接受、忽略 |
| `delivery` | 报告生成失败（结论不受影响） | 重新生成 |

一个决定只重做依赖它的部分：排除一份文件只改送审范围并重新报价，换客户才重新匹配全部要求。哪些要重做由服务端决定，客户端不推断。

文件夹连同各级子文件夹一起读取（最多向下 5 层、200 份文件），每份文件带着它所在的子文件夹路径；超出层数的子文件夹列为问题，需要时单独给出它的链接。内容相同的文件只审一次。来源变化按文件的增删和改名判断。

## 报价与确认

报价（`result.quote`）是确认将启动的内容：送审与参考文件数、标准或深度模式、预计点数与耗时。`quote_id` 随文件、要求版本、模式或计费依据的任何变化而变化。`confirm_audit` 带上用户同意的 `quote_id`；它与当前报价不符、文件夹在准备之后变了、或任务不在待确认状态时，不会开始，并指回 `get_task`。

开始审核、人工复核、回答原件事实、重新生成报告和交付报告，都由用户在浏览器确认页确认一次。页面由服务端生成、不加载脚本，用白话写出这次会发生什么，不显示内部编号。确认后，客户端用同一个 `request_id` 再调用一次。已确认的内容在执行前变了，返回 `confirmation_content_changed`，需要重新确认。人工复核与原件事实的回执和写入在同一事务保存；机器结论与人的判断分开保存。

## 写操作与恢复

每个写操作带一个新的随机 UUID `request_id` 并一直沿用。任务编号由当前用户和 `request_id` 一起决定，不同用户用了同一个编号也不会落到同一个任务上。同一编号、同一参数再次调用，读回原结果；同一编号换了参数，拒绝。结果未知时不换新编号：重复原调用，或用 `get_operation` 读回执。交付到飞书的回复丢失时保持未知，不再发送。一次调用被取消，不表示已受理的审核被取消。

外部服务暂时失败由服务端自己重试；只有需要人参与时，客户端才会看到问题或 `recover`。未知、空结果或执行错误不会变成 PASS；NG 是结论，UNABLE 是证据不足以判定，ERR 是检查本身失败。

复审（供应商补件后）：`prepare_audit` 带上 `follows_task_id`。新资料成为新的任务和新的轮次，与上一次关联，历史结果不被覆盖；同一份飞书文件再次送审时，可以沿用上次审核的要求版本，默认用较新的版本。

## 服务与授权

设置 `PPAP_MCP_ENABLED=true` 后，API 进程提供 `/mcp`（Streamable HTTP，只有工具，没有 Resources 与 Prompts）。生产 `PPAP_PUBLIC_API_URL` 为 `https://ppap.icoastline.com/ppap` 时，服务器地址为 `https://ppap.icoastline.com/ppap/mcp`。开发环境仅允许回环地址使用 HTTP。

授权服务器为 API 地址下的 `/oauth`：客户端注册、授权码与 S256 PKCE、Scope 选择、刷新和撤销。授权及令牌请求的 `resource` 必须精确等于 MCP 服务器地址。用户用飞书登录，核对客户端回调和权限：客户端申请的权限默认全部勾选，用户可取消；开始审核、复核、发送仍逐次在浏览器确认。权限不足时工具在 `next.message` 说明缺哪一项以及如何重新连接。注册时未指定范围的客户端可申请全部范围，实际授予以用户勾选为准。飞书凭据始终留在服务端，读文件夹和导入都用用户自己的飞书授权。

OAuth 发现地址按 RFC 8414 / 9728 放在域名根目录：

- `https://ppap.icoastline.com/.well-known/oauth-authorization-server/ppap/oauth`
- `https://ppap.icoastline.com/.well-known/oauth-protected-resource/ppap/mcp`

部署代理必须把这两个地址交给 API，不能按版本 Cookie 转发到网页；`deploy/tke/edition-proxy.conf` 保留这一边界。需要从其它网页直接调用时，把该网页的完整 HTTPS Origin 加入 `PPAP_MCP_ALLOWED_ORIGINS`，不能用通配符；浏览器 Cookie 不能当作 Bearer。

用户可在 `/mcp/connections` 撤销连接。撤销后旧 Bearer 与刷新令牌立即失效，准备中的任务停止推进；已经受理的审核继续按原任务执行。

飞书凭据由浏览器会话和 MCP 委托共同引用，服务端串行续期。`provider_refresh_in_progress` 与 `provider_temporarily_unavailable` 以 `recover` 返回，稍后重复原调用；`provider_reauthorization_required` 表示凭据失效，用户需重新登录飞书并重新连接。撤销一个连接不影响其它连接。

## 接入

任何支持 Streamable HTTP 与 OAuth 的 MCP 客户端都可直接连接服务器地址，例如：

```sh
codex mcp add ppap --url https://ppap.icoastline.com/ppap/mcp
codex mcp login ppap --oauth-client-registration dcr
```

豆包工作在管理后台新建 MCP 连接器：认证类型 MCP OAuth 2.1，传输类型 streamableHttp，地址同上，请求头 `Authorization: Bearer ${access_token}`；再把 [`deploy/doubao/ppap-audit`](../deploy/doubao/ppap-audit/SKILL.md) 打包上传为技能并关联。客户端是否按要求发送 `resource`，以真实联调为准，不为迁就客户端放宽校验。

停止使用时，先在 PPAP 连接管理页撤销委托，再清除客户端保存的凭据；只删除客户端配置不会撤销服务端委托。

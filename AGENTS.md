# PPAP

本仓是最终产品运行仓。本文件是本仓唯一的产品、架构、数据、安全与流程说明；各目录的 README 只写本目录怎么用。当前行为以源码、数据库约束和测试为准，工作状态只在 GitHub Issues。

开始一项工作时，先读当前 owning Issue、直接相关源码和测试，再读本文件中相关的一节。

## 工作方式

- **资产**：`ppap-agent` GitHub 组织中的代码、配置、文档、测试、样本，以及我们能访问的远程环境，默认都是本项目可直接使用的授权资产，除非产品负责人明确排除。这不改变运行时边界：Secret 不能泄露，租户和对象访问必须经过产品授权，真实外部写操作按业务影响处理。
- **权威**：产品目标是本文件与当前 owning Issue；当前行为是源码、数据库约束和测试；配置是 typed Settings、[`.env.dev`](.env.dev) / [`.env.production`](.env.production) 默认值与 [`.env.example`](.env.example)。Issue 是验收合同，不是实施顺序；一个 PR 可以推进多个 Issue。
- **目标**：尽快完成迁移和开发，让客户尽早使用。走最短正确路径：复用现成资产 → 迁入本仓 → 跑 owning Issue 定义的测试 → 修 regression 或产品缺口 → 打通客户路径。一个内聚的包、测试集或配置整体迁入更快时就整体迁入，再删除无消费者部分。
- **删除**：删除或替换功能时，同时删除只为它存在的测试、文档和配置。不新增 roadmap、CURRENT、Story/proof/evidence registry、manifest 或第二套状态台账；历史在 Git 和已关闭 Issue 里。
- **最小上下文**：按 [context-reduce](https://github.com/ronhuafeng/skills-release/tree/main/catalog/engineering/context-reduce) 执行：只读本次需要的源码、测试和本文件相关一节；非必要不读旧分支、历史设计、旧 Issue、agent 日志或迁移记录。消除无当前消费者的代码、兼容层、重复概念和文档；用小范围测试守住真实不变量，重要外部效应必须读回验证。
- **受保护资产**：检查程序按公司组织，目录叶子在 `audit/checkers/<公司>/`（`uaes`、`li`、`nio`），各自运行 `audit/checker_lib/<公司>/` 的读取实现；叶子只通过声明的资源调用本公司实现。数据标准化预检（`product.precheck*`）独立于检查程序，规则数据对齐 `assets/uaes/prechecks` 并由测试核对原文摘要。这些源码、规则、资源，以及对应的测试、fixtures、样本（`assets/<公司>/`）和人工 Oracle（`assets/corpus/`）都必须保留，不得删除、清空或篡改，也不得改写已入库的不可变 Revision。清理只限有证据表明无当前必要性的旧执行基础设施、隐式 fallback、重复注册、无效兼容层及其过期说明。

## 必须保持

- 所有数据访问执行租户与对象授权；同一企业成员能看彼此的审核，修改只限对象创建人。
- 审核轮次与执行尝试分离；技术重试不制造新轮次，显式重审才创建新轮次。
- 受理、冻结输入与入队在一个事务里；晚到 Worker 不能覆盖当前结果。
- 历史轮次引用明确的文件版本和规则版本，后续上传或发布不改旧轮次。
- 机器结果与人工复核分别保存；未知、空结果、未覆盖或执行错误不能变成 PASS。
- 一轮审核对用户只显示一个总体结论，由后端给出：有人工确认时以人工确认为准，否则显示机器结论；未知或待审核不能显示为通过，前端和插件不自行推导。
- 对外的非幂等操作遇到未知结果时先读回或对账，不盲目重试。

## 产品

**承诺**：质量工程师上传 PPAP 资料，确认系统为每份文件匹配的客户规则，离开后仍能回到同一任务，查看逐文件、可对照原件的审核结果与报告。打开一次审核，就知道审了什么、结果是什么、依据在哪里、下一步做什么。桌面浏览器是主入口，只适配桌面；飞书（H5、云文档插件、飞书项目、MCP）进入同一套产品事实和轮次，不建第二套业务台账。

**旅程**：上传与检查 → 确认规则 → AI 审核 → 报告与历史。
- 不要求先建批准事项、填供应商/工厂/修订或配置 Agent。每份选中的文件分别匹配实际生效规则，确认后冻结文件版本与每文件规则快照；多文件类型不能被悄悄改成参考文件。
- **每个审核任务由用户明确选择一个已登记客户（UAES、理想、蔚来）的规则**，没有自动识别：不从文件写的公司名推断客户，也不拿它拦截；未选客户不能开始识别或审核。企业可保存一个默认客户，任何已认证成员确认后可改；默认只预填，任务内可改选且不改企业默认；每个任务一个客户。受理时冻结模式、文件角色、实际规则与检查程序版本，之后改默认不影响已有任务。
- V4 预置 UAES 19、理想 17、蔚来 17 个「客户 × 规则条目」（`packages/audit/src/audit/preset_rules.json`），按各客户文件类型集 M 提供；蔚来 M 为 N01–N05，N06–N17 及其检查程序保留供历史读取。T12 是否作为待审文件由本轮选择和预置规则决定，作为参考不计费；E12 PGQP1 只按已登记的跨文件对端使用。29 份客户模板用于检查程序调校、测试和模板库展示，不是运行时必选模板。
- 新受理审核只运行明确选定并冻结的检查程序修订、条款绑定和程序字节。缺源、无读取能力、不支持或执行失败都不回退到其他版本、客户或通用检查程序；不支持和证据不足为 UNABLE（未检查），技术失败为 ERR。
- **深度模式**是任务级选项，受理时冻结：先运行已验证的检查程序，再由模型逐条复核；复核不修改、不生成程序，只能以可在冻结原件定位并经核验的证据订正结论，订正标为「深度复核订正」并保留程序原结论；某份文件复核失败或超时即保留程序结果。计费与标准相同。
- **数据标准化预检**按任务当前状态读取，不调用检查程序或模型、免费、不改变受理，可导出 PDF；待审文件有文件级阻断时界面暂不允许开始审核。
- 任务进度、文件执行、业务判断、检查覆盖、报告生成和费用分别表达；未知、未覆盖和技术失败不能冒充通过，也不一律当作不符合。没有待审目标时用「结束任务」，不建轮次、不收费、不出报告。审核结果与报告状态分开，失败报告可重新生成，只恢复同一冻结结果的产物。
- 人工复核是独立选做项，NG 可正常结束；显式补件或重审创建关联的新任务，旧任务与冻结结果不改；正式批准不混入审核结果。费用、权限等政策以产品负责人的明确决定为准，不由界面默认值推导。
- 导航：PPAP Audit、审核文件、模板库、审核规则库、用量记录。模板库列出平台维护的各客户内置模板（只读原件，可下载或存到个人飞书）；「补充模板」只记录提交，不自动进入模板库或规则。
- 示例数据只用于明确的预览或测试通道，不产生真实审核结果、收费、通知或报告。

**术语**

| 词 | 含义 |
|---|---|
| 待审文件 / 参考文件 | 本轮要产生检查结果的文件 / 判断上下文，被引用不等于已审核 |
| 文件版本 | 一次明确上传或导入产生的版本；历史轮次引用它 |
| 审核任务 | 从识别、确认到看结果的用户流程，不等于内部执行工作 |
| 审核轮次 | 固定文件版本、资料角色和规则版本的一轮检查 |
| 检查项 / 检查结果 | 规则要求 / 机器结论，与未知、未覆盖、技术错误分开 |
| 依据 | 原始资料及可定位位置；模型解释不能冒充原文 |
| 人工复核 | 发起人对具体结果的判断、理由和下一步；不改写机器结果 |
| 正式批准 | 组织授权人的正式决定；当前产品不实现审批制度 |

**迁移与范围**：现有 PPAP 系统的客户可见能力是迁移下限，本节是最终目标；验收同时满足 engine 不新增 regression、飞书客户功能不减少、产品闭环完成。`ppap-agent` 长期保留：它有其他用户，也是新审核算法的验证基地，算法先在那里验证再进本仓。不重建通用组织平台，不迁入完整 FMEA/PQM/8D，不另建群聊审核或多 Agent/workflow 平台；当前不迁移旧审核历史。

## 架构

```text
Web（apps/web）/ 飞书入口 / MCP
        |
     apps/api（唯一公开 HTTP，含 /mcp）
      /      \
PostgreSQL  对象存储
      \      /
    apps/worker（执行）→ Deep Controller（可选，python -m agent）
        |
     packages（product、audit、agent、documents）
```

- `apps/api` 拥有身份、会话、授权入口和全部 HTTP 合同；跨包的产品编排归 `product`，共享持久化归 `product.store`。重解析、转换、模型调用和规则检查在 Worker。Web 只调 Product API，不直连数据库或对象存储。
- PostgreSQL 的 `product` 存产品事实，`engine` 存执行工作与尝试，二者不是两套台账；对象存储存文件版本引用的字节。派生工作簿、页面图像或 OCR 结果只在有消费者时缓存，并指向来源文件版本。
- `product.provider_authorizations` 是飞书凭据的唯一续期入口；浏览器会话与 MCP 委托各自有有效期和撤销，引用同一条凭据记录，不按用户找「最新令牌」替换。Worker 不可读。
- 有请求 ID 的飞书与飞书项目导入共用 `_receive_import_upload` 和 `file_versions.upload_request`：来源绑定先于外部读取，内容摘要先于对象写入，恢复先核对已有对象。
- **执行**：Worker 用短事务领取工作（含过期租约重试，同一轮只增加 attempt），事务外执行，用当前 attempt 提交。审核执行 job 中冻结的 `ppap-checker-core` 与 Suite 快照；深度模式在一个 guest 里按文件执行，同组上下文共享，每份文件各自计时（`agent.execution.budget`：检查程序 3 分钟，深度复核 6 分钟），guest 每开始一个任务就报告进度，宿主每次再给一个任务时长（两者取大再加 1 分钟，即 7 分钟），超过它没有新进度才判定卡死，Worker 侧再多等 30 秒；一轮没有总时限。Worker 经有界 UDS 调 Controller，不持有 Docker socket；协议 `extra=forbid`，不接受路径、命令、挂载、镜像或环境变量。整次失败写结构化 failure，不伪造判定、不回退。生产要求 `PPAP_DEEP_RUNTIME=container-only` 与 `PPAP_DEEP_SANDBOX_PROFILE`（`off` 是降权容器，不是 gVisor/VM）；细节见 [deploy/deep](deploy/deep/README.md)。
- 深度与标准受理都把实际执行配置的摘要写进 job（`DeepExecutionPolicy`、`standard-v1:`），执行前由真实 runtime 再核对，配置变化不替换旧轮次的策略；摘要不证明远端模型权重不变。
- 原件预览可用 OnlyOffice（只读、单文件单租户十分钟凭证，Document Server 属于可信边界），没有时回退到有上限的摘录；`.doc` 在 Worker 里经受限的 LibreOffice 转换，没有运行时就明确失败。
- **通知**（审核任务、报告 ZIP、规则问卷卡片）复用 `engine.jobs`，意图与业务事务一起提交，只由 API 进程领取和发送；Worker 不领取通知，也不持有飞书凭据。发送前以当前 claim 持久化发送边界；越过边界后超时不能证明未送达，未知保留，不自动重发。有效消息 ID 才算发送成功，不代表已读。待确认卡片只在完整列出文件、规则、移除原因与预计点数时提供直接确认，回调经原消息与当前创建人身份走同一受理事务。并发模型见 [NotificationDispatch](docs/models/NotificationDispatch.tla)（只证明安全性，不证明送达）。
- 外部能力（飞书、Partner AI 计费、LLM/OCR、Deep Controller）都是可选能力，不成为 Core 启动或普通 CI 的前置。只有当前需求证明价值时才新增服务、框架或隔离层。

## 数据

| 事实 | 唯一拥有者 |
|---|---|
| 用户、租户、外部身份、成员 | product（不复制组织树） |
| 文件与文件版本 | product + 对象存储（数据库拥有版本身份和对象引用） |
| 草稿、目标/参考角色、规则版本 | product（草稿可改，受理后固定） |
| 审核轮次、结果、依据、人工复核 | product（机器结果与人工记录分开） |
| 执行工作、尝试、租约、诊断 | engine（不等于客户看到的任务） |
| 使用事件 | product（外部计费以确认结果为准） |
| 规则库修订、问卷、答复、解读 | product（见「规则处理 SOP」） |

```text
audit_round
  -> target file_version_id + frozen rule_version (id/title/body)
  -> reference file_version_id（无审核规则，仅上下文）
  -> job/attempt（冻结 executor / executor_revision）
  -> machine findings + locators
  -> human reviews
```

- **文件版本**：每次明确上传或导入创建新的 `file_version_id`；已引用的版本不能被重定向。hash 只用于比较与完整性，不是版本身份。飞书导入的 file origin 与写回目标（writeback target）分开保存。本地上传先存入个人飞书目录，回执未知时保留原请求、不盲目重传。
- **受理**：每个待审文件在受理时绑定明确规则版本（草稿可暂缺），参考文件不附规则；规则 id/title/body 与条目执行映射随轮次冻结，不能映射的条目进 `coverage.unchecked`。相同租户、幂等键与输入返回同一轮次，同键不同请求冲突；幂等键不是授权。规则客户范围分指定客户、明确不限客户和未确认三种，旧版空客户列表不推导为不限；多候选冲突不静默消解。
- **重审**：读取 `GET /rounds/{id}/reaudit-inputs` 不新建业务；新任务经 `predecessor_id` 关联来源，不复制人工判断或机器结果；原文件可沿用原已发布规则，有新版时默认用新的并提醒；替换文件用 `replacement_of_file_version_id` 对应原输入；改选客户须重新识别。
- **标准执行映射**：标准审核把用户确认的条款按原顺序绑定到该类型、该 profile 的固定顺序检查器，不做条款全文或语义裁决；超出可用检查器的条款标为未覆盖。只影响新受理。
- **结果**：实际 PASS/NG 决定计数、文件结论和报告；证据核验标记、定位和范围说明是独立信息。不标「证据已验证」，机器证据一律提示对照原件核实；可执行或可重放不证明解释正确。显示更正按 Task、Round、FileVersion 共同定范围，与原始识别分开保存，不改冻结结果或报告。
- **原件事实确认**（`/audit-rounds/{id}/fact-answers`）：回答绑定轮次、文件版本、字段和所见问题，单独保存，不覆盖机器结果、不入队、不耗模型；当前只有 UAES 检查程序在单页 PDF `visual_result` 的全满足布尔检查上产生可重算的事实，其余只保存回答、结果仍为 UNABLE；人工确认 PDF 单独下载。
- **深度模式数据**：`audit_drafts.deep_mode` 受理前可改，冻结到 `audit_rounds.deep_mode`；重审默认关闭；没有 Deep 运行环境或编码模型时拒绝开启和受理（`deep_mode_unavailable`）。订正须附可定位证据，guest 对照冻结原件逐条核对（扫描页不能作证据），宿主再核对；`locator.semantic.deep_review` 记录认可/订正/作废，`checker_execution.targets[].deep_review` 记录文件级完成或 `degraded`。Deep 规则附录是用户确认的解释、映射或标准修改，带文件/条款范围与来源，按 `{document_id, rule_id}` 限定，每次分析冻结完整附录。
- **报告**：完成事务中为每个文件结果创建唯一 `report_pdf` 工作，固定本轮文件、规则、机器结果，不含人工记录；对象按工作与 attempt 隔离，只有获胜 attempt 可发布。ZIP 复用 `engine.jobs`（`report_zip_request` / `report_zip`），同一规范化集合只关联一个工作，恢复绑定原请求。报告分享冻结已授权报告，只在同组织已登录成员内可读。
- **授权**：同一企业成员都能查看该企业的任务、轮次、原件和历史；只有创建人能修改它以及挂在上面的一切（确认与开始审核、人工复核、事实确认、显示更正、报告重生成、写回与放行、文件新版本）。创建人取自各表的 `created_by`，不另存授权表；他人可基于同一结果发起自己的重审。UUID 不代表授权。未提交的上传准备只列给创建人；暂存上传完成前不能被受理。「补充模板」记在 `template_submissions`，同一 `request_id` 重放返回同一记录。
- **用量与计费**：只记录实际业务事件与外部确认状态。收费开启时，受理按匹配且选中的待审文件数 × 500 点生成用量（参考文件不计费），服务端读飞书 AI 开关与发起人额度，至少一池有正余额即可；深度与标准同价，每个文件一笔 500 点。显式重审产生新消费，技术重试和受理回放不产生。上报先原子领取发送权，发送后未知记为 unknown，不自动重发；飞书用量回执不是扣费凭据，实际扣费凭据（`debit_order_id`、`debited_at`）尚未接入，已扣/待扣在有未知时保持未知。`GET /audit-usage` 把同一轮次、文件版本的免费/付费分片投影为一笔 500 点，按受理月（Asia/Shanghai）归集，补扣不改月份；`external_confirmed_at` 是 PPAP 保存外部回执的时间，不是飞书扣费时间，历史空值不回填。剩余试用 = 配置额度 − 已确认 cut-over baseline − 已消耗试用。

## 安全

- 各进程用最小运行权限；外部身份按 provider/app/tenant/subject 绑定；登录不等于跨租户访问。PostgreSQL 连接默认自动提交，读改写必须显式开事务。
- **MCP 委托**绑定身份、客户端和固定资源地址；Scope 只缩小当前用户权限，每次调用和任务推进的每一步都重新检查授权。浏览器 Cookie 不能直接调 MCP。授权码用 S256 PKCE 且只能兑换一次；刷新令牌轮换，重用旧刷新令牌会撤销整个委托。飞书凭据续期先持久 claim，并发返回 `provider_refresh_in_progress`；中断后不释放刷新权，要求重新登录。
- **确认页**：开始审核、人工复核、事实回答、重新生成报告、交付报告，以及规则澄清的确认类动作，都在服务端生成的无脚本页面上由本人确认；确认绑定具体请求，CSRF 绑定当前会话，执行前重新检查来源与显示内容，网关把 `ApprovedCall` 放进内部 scope，业务入口核对后使用已捕获内容，不再读可变来源替换。授权表单只向同源提交（`form-action 'self'`），完成后经 HTML refresh 跳回已验证的回调。`next` 只是建议，不构成收费或对外发送的授权，也不含被审文件内容。
- **文件与输入**：文件只经上传、飞书或飞书项目导入进入，不抓任意网址；扩展名不是安全判断；文档内容是数据不是授权，不能让模型或解析器获得工具、网络或凭据。
- **Secret**（API key、app secret、verification token、session、生产数据库和对象存储凭据）只经 gitignored `.env`、GitHub Secrets 或部署 Secret 注入；日志不输出 token、cookie、signed URL 或客户原文；反向代理不记录 OAuth 查询串。
- **模型凭据**：平台 Key 只在部署 Secret；企业 Key（BYOK）由运营用 `python -m product.tenant_models` 从标准输入写入，用 `PPAP_MODEL_CREDENTIAL_ENCRYPTION_KEY` 加密并绑定租户、地址、模型与配置修订，地址须在 `PPAP_TENANT_MODEL_ALLOWED_BASE_URLS` 精确批准（不跟随重定向、不用代理、不接受本机与特殊地址）。企业 Key 不可用时报告不可用，不回退平台模型；受理时冻结非秘密模型身份，不一致即 `model_configuration_changed`。Deep guest 只拿本地网关的短期 Grant，接触不到真实 Key。
- 外部效果只在路径真正产生外部效果时才考虑幂等、未知结果和读回。LLM、飞书、Partner AI 等远程环境可直接用于开发验证，其临时不可用不阻塞无关路径。

## 审核 SOP

**入口**：都进入同一套任务、轮次和结果，口径一致。

| 入口 | 谁用 | 资料从哪来 | 说明 |
|---|---|---|---|
| 网页 `/tasks/new`（经 `/enter-current`） | 质量工程师 | 本地上传或飞书云文档选择 | 主入口；`/files`、`/requirements`、`/usage` 分别是文件、规则、用量 |
| MCP | 用户的 AI 客户端（Claude、Codex、豆包等） | 只收飞书文件夹或文件链接 | 合同见下节「MCP」 |
| 云文档插件 `/addon` | 在飞书文档里 | 当前文档关联的文件 | 用 `POST /login/feishu/addon` 换 2 小时 Bearer 会话，不用 Cookie |
| 飞书项目插件与 AI 节点 | 在飞书项目工作项里 | 工作项附件（整包） | 见 [apps/meego-plugin](apps/meego-plugin/README.md)、[apps/meego-ai-node](apps/meego-ai-node/README.md)；身份须在 PPAP 确认页绑定，结论以带轮次标记的评论写回 |

**一次审核怎么走**
1. **选客户并导入资料**：文件逐份冻结为文件版本；飞书导入用用户自己的授权，按来源推导请求 ID，重做读回同一次导入。
2. **识别与匹配**：按所选客户的规则识别文件类型、匹配规则；预检同时给出阻断与警告。客户、文件或角色改变只重做依赖它的部分。
3. **确认与受理**：确认页列出送审文件、规则、模式和预计点数；扣点发生在受理时。经 MCP 或插件时，本人在浏览器确认；飞书待确认卡片在满足条件时可直接确认。
4. **执行**：Worker 运行冻结的检查程序（深度模式再逐条复核）；进度、结果、覆盖和报告状态分别显示。
5. **看结果**：逐文件结论、检查项、原件定位；需要时记录人工复核、回答原件事实、处理建议复核提示（`PUT /rounds/{id}/review-hints`，不调模型、不改结果）。
6. **交付**：报告 PDF、ZIP、组织内分享、飞书报告文档（MCP）、飞书项目评论写回（插件）；未知回执只读回，不重发。
7. **补件重审**：新任务关联上一轮；有新规则版本默认用新的并提醒，可沿用；重审按整包计费。

**线上验证**：见「发布与线上验证 SOP」。真实受理会扣点，验证前说明点数；能用预检、报价、结果读取验证的，不发起新审核。

**现在实现的不足**（做相关工作前先看）
- 实际扣费凭据尚未接入，已扣/待扣在有未知时保持未知，不能自动对账。
- 飞书回调只支持 HTTPS 下的 Verification Token 校验，不接受加密回调；通知与分享没有长期 exactly-once 保证。
- 扫描 PDF 不做整份表格转录；多页联合视觉事实（全图一致、所有签章齐全）在多页时保持未知；DOCX 图形事实暂返回 UNABLE。
- 原件事实确认只对 UAES 单页 PDF 的全满足布尔检查能重算结论。
- 审核用的规则仍来自检查程序目录叶子，已澄清的规则库修订不会自动进入审核（见下节）。

## MCP

用户给出一个飞书文件夹，PPAP 在服务端把它准备成一次可确认的审核；只有人能决定的事才问人，决定后自己接着做。工具与参数形状以服务端注册为准。

- **任务是中心**：持久状态机，记着资料来源、每份文件的导入和用户的决定。`product.workflow` 的 `apply`（事件→状态）、`effects`（状态+事实→要做的事）、`project`（状态+事实→阶段、问题、下一步）不做读写；`api.mcp_runtime` 经 Product API 执行副作用，请求编号由任务编号推导，中断重做不重复；状态存在 `product.audit_workflows`，按修订号比较写入。文件之间互不依赖、结果累积；阶段之间有依赖，一个问题只挡住依赖它的部分；中断是常态，任何时候读它都得到同样的问题和下一步。
- **工具**：`find_submissions`、`prepare_audit`（必须带用户选的客户 `key`）、`get_task`、`get_issue`、`resolve_issue`、`confirm_audit`、`get_results`、`get_finding`、`record_review`、`publish_report`、`find_audits`、`get_operation`、`get_account`（含各客户的规则库版本）、`prepare_rule_clarification`。只有工具，没有 Resources 与 Prompts；MCP 不传字节，没有检查程序工具，不改审核用的规则。每个面向用户的路由要么由工具到达，要么在 `apps/api/tests/test_mcp_coverage.py` 写明为何留给网页。网页或插件发起的审核同样能用 `get_task`、`get_results`、`record_review`、`publish_report`。
- **每次返回** `result`（已确定的事；`phase` 取 `preparing`、`awaiting_decision`、`ready_to_confirm`、`auditing`、`completed`、`closed`，各阶段状态分开表达）、`issues`（带 `allowed_actions`）和 `next`：`wait`（`check_after_ms` 后再读）、`user_action`（交给用户；带 `url` 的去浏览器确认后用同一 `request_id` 再调）、`call_tool`、`recover`（用同一 `request_id` 重复或读回执）、`done`、`blocked`（`message` 原样转述）。`next` 是建议不是授权：`confirm_audit`、`record_review`、`publish_report` 只在用户明确要求时调用。
- **问题与决定**：问题由状态推导，起因消失即消失；`resolve_issue` 只接受列出的动作并带 `task_revision`，过期、已关闭或不在列表的一律拒绝。类别有 `access`、`import`、`source_changed`、`rule_conflict`、`missing_evidence`、`quote_changed`、`fact_confirmation`、`review`、`delivery`。文件夹连同子文件夹读取（最多 5 层、100 个子文件夹、200 份文件），内容相同的文件只审一次。
- **报价与确认**：`quote_id` 随文件、规则版本、模式或计费依据变化而变；与当前报价不符、文件夹已变或不在待确认时不开始。确认后内容变了返回 `confirmation_content_changed`。
- **写操作**：每个写操作带新的随机 UUID `request_id` 并一直沿用；任务编号由用户和 `request_id` 共同决定。同编号同参数读回原结果，换参数拒绝；结果未知不换编号。复审用 `follows_task_id`。
- **服务与授权**：`PPAP_MCP_ENABLED=true` 时 API 提供 `/mcp`（Streamable HTTP）；生产地址 `https://ppap.icoastline.com/ppap/mcp`。授权服务器在 API 下的 `/oauth`，`resource` 必须精确等于服务器地址；客户端申请的权限默认全选，用户可取消。发现地址 `https://ppap.icoastline.com/.well-known/oauth-authorization-server/ppap/oauth` 与 `.../oauth-protected-resource/ppap/mcp` 必须由代理交给 API（`deploy/tke/edition-proxy.conf`）。其他网页直接调用时把完整 HTTPS Origin 加入 `PPAP_MCP_ALLOWED_ORIGINS`。用户在 `/mcp/connections` 撤销连接，已受理的审核照常执行。
- **接入**：任何支持 Streamable HTTP 与 OAuth 的客户端都可连接，如 `codex mcp add ppap --url https://ppap.icoastline.com/ppap/mcp` 后 `codex mcp login ppap --oauth-client-registration dcr`。豆包工作建 MCP OAuth 2.1 连接器，并上传 [`deploy/doubao/ppap-audit`](deploy/doubao/ppap-audit/SKILL.md) 技能。停止使用时先撤销委托，再清客户端凭据。

## 规则处理 SOP

**三样东西，各有一个权威，不要混用**

| 是什么 | 权威 | 谁改、怎么改 |
|---|---|---|
| 审核用的规则（受理时冻结进轮次） | 检查程序目录叶子的 `packages/audit/src/audit/checkers/<公司>/<叶子>/rules.json` → `scripts/compile_v4_presets.py` 编译成 `packages/audit/src/audit/preset_rules.json`（CI 用 `--check` 防漂移）→ 企业成员登录时装进 `product.rule_versions` | 线下改叶子并编译，随发布生效；产品内没有入口 |
| 检查程序 | 目录叶子的 `checker.py`/`resources.py`，按客户成组；官方版本经 `product.seed_standard_suites` 与 `python -m scripts.official_suites` 验证、发布（见 [tests/README](tests/README.md#官方-suite-验证与发布)） | 线下，平台运营；企业采用版本经 `PUT /suites/{key}/active`（只给平台运营，无网页入口；成员可用 `GET` 读当前版本；无指针时返回 `candidate_only`，未验证候选须确认风险；发布回执未知只读回） |
| 规则库修订（澄清中的客户规则） | `product.rulebook_revisions`（每个企业一份；条款存在 `rule_versions`，带 `rulebook_revision_id`，不进入审核匹配） | 规则澄清循环（MCP）或导入命令；写入后冻结 |

另外两条独立路径：企业自定义规则经 `POST /rules`、`/rules/parse`（只给平台运营，无网页入口；发布为企业自己的不可变 Suite，条款先全部为未检查，检查程序由平台线下准备后作为新修订提供；同一前置规则已有后继时返回 `suite_head_conflict`），数据标准化预检（`product.precheck*`）不读规则、不调检查程序。网页规则页对这些都只读，唯一的写是客户成员作答问卷。MCP 的审核流程只按客户 key 选规则组，不改规则。

**版本与修订**
- 版本（v1.0、v2.0……）由条款集合决定：增、删、拆分、合并条款就升版本，只升第一位。修订（修订 0、1、2……）是同一版本内只改条款内容。客户的新稿也按这条判断。
- 条款标识是「客户 + 版本 + 文件类型缩写:编号」，缩写和编号照抄客户原件；同一版本各修订的编号集合相同。
- 修订写入后冻结，只能之后记下「已澄清」。每处改动都要写明意思变没变和依据（卡片与答复人，或客户裁决原话）；说不出依据的改动不进修订。经他人转达、没有客户原始答复的口径不算客户已确认。
- 确认人是产品负责人（平台运营身份 `PPAP_PLATFORM_OPERATORS`，格式 `tenant_key:open_id`，空即无人可写）。受委托的审查方（`PPAP_CLAUSE_REVIEWERS`）只准备、不确认、不作答。

**澄清一轮怎么走**
1. **收规则**：客户规则线下发来。平台按原件拆成修订目录 `<客户>/rulebook/<版本>/m<修订>/rules/<缩写>.json`，用 `python -m product.rulebook_import check <目录> --original <原件>` 逐句核对原文（不调模型；Markdown 排版与空白不计）；通过后 `bundle` 输出请求体（stderr 给出路径），平台运营身份登录 PPAP 后在同一站点把它 POST 到 `/ppap/rulebooks/<客户>/revisions`（Cookie 会话）。也可由 Claude 经 MCP 提交拆好的条款，平台负责人在浏览器确认。不做在线导入。随产品发布的规则库目录（`assets/<客户>/rulebook`）在企业成员登录时原样写入。
2. **审查**：经 MCP `prepare_rule_clarification` 打开这个版本的澄清任务（一个企业对一个客户的一个版本只有一个），按 `next` 走。每条条款用 [`clause-check`](.agents/skills/clause-check/SKILL.md) 审查并提交审查记录，没有问题也要提交；服务端用该技能的 `check.py` 校验，不合格之处一次列全。效果用 [`bench/clause-check`](bench/clause-check/README.md) 对比。
3. **发问卷**：发现由该技能 `questionnaire.py` 的同一个函数做成卡片（修订 0 的卡片编号是发现编号如 `CP:8#1`，之后的修订带修订号如 `CP:8#r1.1`）。平台负责人在浏览器确认后卡片才进入问卷，可分批追加；答复过这个版本问卷的人收到飞书通知，第一次发时没人答复过就不通知。要让另一个企业也作答，用 `questionnaire.py` 以迁移身份线下导入，各企业答复分开保存。答复按人分开、追加保存，不设最终答复。
4. **处理答复**：对有答复的条款，按答复改写并写明依据的卡片，或说明不需要改，或追问（`follows` 指向原卡片）。答复不一致由产品负责人取舍。
5. **出新修订**：平台负责人确认新写法，成为下一修订并冻结，上一修订的问卷关闭、答复保留。只重新审查原文改过的条款；已答清的不再问，没人答的卡片进入新问卷。
6. **收敛**：每条都已审查、没有未决问题，由产品负责人确认「已澄清」，不能从已答数量推断；再逐条配解读（查什么、何时通过、何时不通过、何时转人工），保留原文的引号用语和数字，原文仍是唯一判定依据。
7. **切换审核**：确认修订不等于审核改用它。把已澄清的修订变成审核用的规则（改目录叶子的 `rules.json`、编译预置规则、让检查程序对齐并按官方流程验证发布），代价高，离线进行，是单独的发布决定；新增条款在澄清前不写检查程序，没登记检查程序的条款在审核结果里显示「未登记检查程序，暂未检查」，不算通过也不算出错。

**用户看到什么**：规则页按客户列出修订（「v1.0 修订 1」），标出已澄清、每条相对上一修订的改动与依据（可跳到对应卡片）、解读和待确认问题。

**现在实现的不足**（做相关工作前先看）
- 已澄清的修订不会自动流向审核：审核规则的权威仍是目录叶子的 `rules.json`，修订在数据库里且按企业保存；没有把修订导出成目录叶子的工具，切换全靠线下手工。
- 修订按企业保存：同一客户的规则在 A 企业澄清过，B 企业看不到；随产品发布的规则库在每个企业各复制一份。
- 解读只显示在规则页：检查程序和深度审核还不读它；检查程序版本变化不会让解读重新打开。
- 受委托审查方的权限只在本地测试验证过；线上目前审查方就是平台负责人本人。
- 澄清没有网页入口，进度只能经 MCP 查看；测试用的修订和问卷没有删除入口，只能用数据库管理身份手工清理。

UAES 规则库的目录、标识和当前状态见 [assets/uaes/AGENTS.md](assets/uaes/AGENTS.md)。

## 开发

- **配置**：按显式 `Settings(...)` 参数、进程环境、指定 dotenv、默认值依次取值，显式 `False`、空字符串不被低优先级覆盖。`.env.dev` 是 isolated-dev 默认（平台运营默认是测试身份 alice、carol），`.env.production` 是生产可沿用的默认（口令、S3、公开地址、LLM 留空，收费关闭）。收费开关 `PPAP_USAGE_CHARGE_ENABLED` 在 Settings 与 `.env.dev` 默认开启，isolated-dev Compose 默认关闭，验证收费时显式打开。新增配置项前先确认它是独立运行决策。可选能力缺配置只影响该能力：Deep 由 `PPAP_DEEP_CONTROLLER_SOCKET` 表示是否配置，未配置时审核执行明确失败。预览切流用 `PPAP_CURRENT_TENANT_ALLOWLIST`（空即不限）。
- **模型来源**：客户不选模型。平台模型分文本 `PPAP_MODEL_TEXT_*`、视觉 `PPAP_MODEL_VISION_*`（缺少时不借用文本配置）、Deep 编码 `PPAP_MODEL_CODING_*`（未配时沿用文本，base URL 与 model 须同时给）；企业 Key 存在时三种用途全部改用它。推理强度 `PPAP_MODEL_{TEXT,VISION,CODING}_REASONING_EFFORT`（编码默认 `medium`，部署取值见 `.env.production` 与 `deploy/tke/current.yaml`）。普通模型请求统一经 `documents.ai.run_json`：最多一次输出校正，不做传输重试，失败只返回代码；Schema 合法不等于业务正确。Deep 的每个 Attempt 只有请求次数熔断 `PPAP_DEEP_MODEL_MAX_REQUESTS`（默认 200）。
- **按规则读取证据**：规则确定所需事实 → 原生提取（Excel、PDF 文字层、可靠表格优先，非扫描 PDF 表格先转 Excel 并保留映射）→ 按具体缺口用 LLM OCR（印章、签字、勾选、照片、图形）→ 程序判定或有来源的文本语义检查。OCR 单次 20 秒超时、零自动重试、最多 4096 tokens，截断或空白为技术失败；同轮同源复用回执。文本模型只接收相关条款和有原件位置的片段，引用须回查。尺寸审核绑定数据、标准、单位与结果，单位不能确定时 UNABLE；跨文件身份完整匹配，不借用相近编号。离线回放和真实调用分别报告，人工样本只作差异参考。
- **命令**：提交前执行一次 `uv run --no-sync pre-commit install`（ruff check/format 与 mypy）。测试归属与运行见 [tests/README](tests/README.md)，CI 以 [Tests workflow](.github/workflows/tests.yml) 为准；数据库迁移见 [migrations/README](migrations/README.md)；部署见 [deploy/tke/README](deploy/tke/README.md)；isolated-dev 见 [deploy/isolated-dev](deploy/isolated-dev/README.md)。GitHub Actions 的外部模型测试用 `CI_LLM_API_KEY`、`CI_LLM_BASE_URL`、`CI_LLM_MODEL`。
- **工具链**：Python 3.12（`.python-version`）、uv workspace（每个 owner 一份 `pyproject.toml`，`uv.lock` 是依赖权威，安装后用 `uv run --no-sync`）；Node 22（`.node-version`）、pnpm 由 `apps/web/package.json` 的 `packageManager` 决定；镜像图以 `docker-bake.hcl` 为准，Compose 只消费镜像；Actions 钉到不可变 commit SHA，只用托管的 `ubuntu-24.04`。生产镜像用 `uv sync --frozen --no-dev` 并以 editable 方式安装工作区包。
- **Web 接口类型**：FastAPI 的 OpenAPI 是 Web JSON 形状的唯一权威；在 `apps/web` 运行 `pnpm api-types` 生成 `src/generated/openapi.ts`，CI 拒绝差异。生成物只拥有类型，重试、文案、缓存和视图状态手写。没有运行时操作的界面合同标 `x-contract-stage: ui-draft`。
- **本地连现网**：本地 Web 或候选 API 可以连现网数据做联调，但写入是真实业务操作；不运行初始化、迁移、测试重置或额外 Worker，会收费、通知或受理的动作按联调范围控制。
- **飞书外部事实**（以开放平台为准，变更后重新核对）：发消息最小权限 `im:message:send_as_bot`，附件上传用 `im:resource`；租户授权须 `grant_status=1`；用户信息接口与字段权限分开；通知卡片用 JSON 2.0（`schema: "2.0"`、`body.elements`、`behaviors`，客户端 7.20+），动态文本须转义；卡片直接确认需 `FEISHU_VERIFICATION_TOKEN` 并订阅 `card.action.trigger` 到 `/integrations/feishu/events`；`app_ticket` 由同一回调接收，过渡期见 [deploy/tke/README](deploy/tke/README.md)。通知总开关 `PPAP_FEISHU_NOTIFICATIONS_ENABLED`；单任务联调用 `dispatch_task_notification(..., task_id=...)`，历史补发须另行授权。
- **深度审核评估**：质量与成本用 [`bench/deep-audit`](bench/deep-audit/README.md) 在从 `HEAD` 构建的深度镜像里对 UAES 人工 Oracle 评估。

## 发布与线上验证 SOP

- **推送前**：在本机跑受影响范围的 CI 等效测试，通过再推：`python3 -m scripts.ci_shards integration`（需要 Docker），`distributions --shard <n>`，以及相关单元测试。本地 API 测试用的开发数据库要先起来。
- **构建**：`deploy/tke/build-current.py <完整 SHA>` 在云效流水线构建，本机只是发起和等待。中断后先查云效最近的运行，能续用就用 `--run-id`，不盲目重发。构建期间不改 `deploy/tke/current.yaml`。改 `pyproject.toml` 或 `uv.lock` 会让依赖基础层失效、要求先重建基础层，非依赖改动不要碰这两个文件。
- **部署**（ppap-test，整套一起）：有新迁移时先更新 ConfigMap `ppap-current-migrations` 并重跑 Job `ppap-current-init-db`，读回 `product.schema_migrations`；再按 [deploy/tke/README](deploy/tke/README.md) 应用 ConfigMap、Deployment 和 CronJob，等滚动更新完成；提交 `current.yaml` 的镜像版本。个人标识和密钥只放根目录 `.env` 与 Secret `ppap-current`，容器只按 key 引用，不进 git。
- **线上验证**：用真实客户端走真实路径，读回页面上看得到的结果；授权页和确认页在内置浏览器里点。会改动真实客户数据、且无法撤回的步骤（如关闭真实问卷、往里加卡片），改在单独的测试版本上验证，并告诉产品负责人留下了哪些测试数据。扣点数的操作先说明点数。

## 前端

- **设计依据**：以当前确认的设计稿为准，文案与设计稿一致。参考外部组件或示例时读它的源码并复制改写，不看截图仿制。
- **视觉值只有一个来源**：颜色、字体、圆角只改 `apps/web/tokens/`，`design.md` 的头部和运行时主题都由它生成，不手改；做法见 [tokens](apps/web/tokens/README.md)，改页面按 `product-design-loop` 技能的流程。
- **数据先找后端**：页面要的数据，先在现有接口和字段里找；找不到才用已有读取推导，最后才加后端。同一概念全站只有一个定义。
- **不给用户看内部信息**：内部 ID、诊断码、内部编号、命令、脚本内容、路径、日志原文都不显示，确需保留的放进悬停提示。用户自己提供的内容（如上传的文件名）可以显示。
- **不让用户思考**：每屏一个主任务，每个区域只有一个主操作；同一信息只出现一次（标题、表头、徽标、面包屑之间不重复）；用用户的词命名，不用实现层面的术语。
- **等待与空态**：加载状态要有可见文字，不能先显示 0、— 之类的假值；长任务显示实时进度，进度窗口有上限，不刷屏。
- **折叠**：用显式按钮加计数，不用 `<details>`。名称被截断时，必须能看到全名（如悬停提示）。
- **可访问名称**：控件的可访问名称只放主名称；状态、数量、说明通过 `aria-describedby` 作为描述提供。
- **一套界面**：替换时直接删除旧页面和旧组件，旧链接跳转到新页面，不保留新旧两版。唯一例外是页面外壳按打开位置区分：浏览器里是左侧导航，飞书客户端里是顶部导航；视觉值、组件和页面内容只有一套（见 design.md「Page Frame」）。
- **验证**：先读代码判断交互，截图只做对照，不要反复刷新去截加载态；看效果时用真实构建产物，在实际运行的应用里检查；预览台和 fixture 不能作为验收依据。

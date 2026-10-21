# 目标架构

产品语义见 [Northstar](NORTHSTAR.md)；当前实现事实以源码和测试为准。

```text
Web / PWA / 飞书入口
        |
   services/api
      /      \\
PostgreSQL  object storage
      \\      /
   services/worker
        |
     packages
```

PC Web 入口在 `apps/web`，只调用 Product API。飞书 docs-addon 进入同一套 round / findings / review。

生产部署可以把 API/Worker 放到 self-hosted InsForge Custom Compute 上，由 InsForge 提供 PostgreSQL 集群与容器生命周期；PPAP 仍是唯一 Product API 与领域 authority。Web 不直连 InsForge Database/PostgREST。细节见 [deploy/insforge/README.md](../deploy/insforge/README.md)。

## 所有权

`services/api` 是唯一公开 HTTP/process adapter，拥有身份/session、授权入口以及文件、草稿、轮次、复核和用量的 HTTP contract；跨 core / engine / store 的产品编排由 `ppap_application` 拥有，共享 PostgreSQL persistence implementation 由 `ppap_product_store` 拥有。API 与 Worker 都组合这些 installable owners，而不是互相作为 library。重解析、转换、模型调用和规则检查由 Worker 执行。

原件预览优先使用可选的 OnlyOffice Document Server，Python API 签发只读配置，不提供编辑回写。`PPAP_ONLYOFFICE_URL` 是浏览器可达地址，`PPAP_ONLYOFFICE_API_URL` 是 Document Server 可达的 Product API 地址，`PPAP_ONLYOFFICE_SECRET` 与其 JWT secret 一致。授权通过后签发单文件版本、单租户、十分钟有效的读取凭证；该凭证在到期前仍可读取，因此 Document Server 及其缓存必须属于可信部署边界。API 和反向代理不得记录凭证查询串。缺少服务时回退到有大小上限的 Word / Excel / 文本摘录；摘录不执行宏、公式或模型，也不转换旧 DOC。审核历史支持服务端搜索和分页。飞书目录选择使用当前用户令牌，逐文件导入独立版本，不递归导入或修改来源。

PostgreSQL 中 `product` 保存产品事实，`engine` 保存执行任务/尝试。两者不是两套产品台账。对象存储保存文件版本引用的字节；数据库保存文件版本、对象位置和授权关系。

一个历史审核只需要稳定引用它当时使用的 `file_version_id` 和规则版本。新上传创建新文件版本，不重定向旧版本。内容 hash 可以作为辅助字段保留，但不是产品版本 identity 的前提。

派生工作簿、页面图像或 OCR 结果只有当前消费者需要时才缓存，并明确指向来源文件版本。浏览器不获得对象存储管理凭据。

## 执行

Worker 用短数据库事务 claim 工作（含过期 `leased` 重试，同一 round 只增加 attempt），事务外执行，并用当前 attempt/claim 提交结果。技术重试不创建新产品轮次；用户主动重审才创建新轮次。

Worker 在一个边界按冻结的 `analysis_mode` 路由：`standard` 走本仓 in-repo engine；`deep` 只通过 bounded UDS 调用独立 Controller。Deep 失败时写回 ERR findings 并将 job/round 标为 failed，**不得静默回退 Standard**。Controller 协议 `extra=forbid`，不接受 host path、command、mount、image 或 env；Worker 不持有 Docker socket。Controller 进程是 `python -m ppap_deep`；当前 production factory [runtime.py](../packages/ppap_deep/src/ppap_deep/runtime.py) 要求显式选择 `PPAP_DEEP_RUNTIME=container-only` 和 `PPAP_DEEP_SANDBOX_PROFILE=off|ppap`，组合 `SemanticRuntime`、`ContainerRuntime` 与 model gateway。guest 命令与镜像不来自 Worker RPC。当前隔离限制见 [Deep runtime](../deploy/deep/README.md)；不能把降权进程称为已交付的 gVisor/VM 隔离。`PPAP_DEEP_CONTROLLER_SOCKET` 只表示 Deep capability 是否配置；Core `/ready` 仍只探 PostgreSQL 与对象存储。

规则检查、模型推断和文档抽取可以替换实现，但不能改变产品输入版本、授权和结果语义。只有真实当前需求证明价值时才新增服务、框架或隔离层。

## Deep 执行环境的演进约束（提议）

本节是长期架构提议，不代表新后端已实现或已经批准上线。实施状态、验收与平台选型决策只在配套 GitHub Issues / PR 中维护，不新增 roadmap 或状态台账。它延续原生 Deep 的 [#67](https://github.com/ppap-agent/ppap/issues/67) 边界，不替代 [#78](https://github.com/ppap-agent/ppap/issues/78) 的客户开放验收或 [#15](https://github.com/ppap-agent/ppap/issues/15) 的生产交付责任，也不为 Standard 增加上线前置。

### 分层，而不是引入通用 Agent 平台

借鉴 AX 的 control plane / runner / sandbox backend 分离；不直接把 AX 变成生产依赖，也不把 PPAP 改造成允许调用方提交任意 Task YAML 的平台。

```text
Product API / PostgreSQL product + engine
        |
Worker: claim / renew / fenced completion
        | bounded UDS: frozen audit intent
Deep Controller: authorization / revision / policy
        |
SemanticRuntime: frozen inputs / semantic validation / product mapping
        | PPAP-owned, minimal execution contract
Execution backend
        +-- ContainerRuntime             existing implementation
        +-- stronger isolation backend   subject to security validation
        +-- AX / Substrate adapter       only after an adoption decision
```

这些是职责边界，不要求新增服务、package 或一套类层次。优先收敛 [controller.py](../packages/ppap_deep/src/ppap_deep/controller.py) 的 `Runtime`、[auditor.py](../packages/ppap_deep/src/ppap_deep/auditor.py) 的 `SemanticRuntime`、[semantic/protocol.py](../packages/ppap_deep/src/ppap_deep/semantic/protocol.py) 的 `SubmissionSpec` 及现有 `ContainerRuntime`；不要平行复制 `DeepTaskSpec`、Workspace registry 或 Model registry。

Worker 只描述“审什么”。Controller 在可信边界内决定实际 runner、固定入口、镜像、工具、资源与网络策略；backend 执行这些受控选择。Backend 即使使用 AX，也只能消费 Controller 编译的 Task，不能把 image、command、env、mount、host path 或 debug 权限透传给 Worker、用户或文档内容。

### 产品身份、语义执行与物理尝试

`round_id`、`file_version_id`、规则版本与冻结条目仍由产品模型拥有。`job_id`、`attempt` 和当前 claim/lease 决定谁可以提交结果；backend handle 只是基础设施定位信息，不是第二个产品任务。

现有 `SubmissionSpec.expected_execution_id()` 是由冻结语义输入及 engine revision 导出的执行 identity。可重复读取已验证结果，不等于重新进行付费模型调用。物理 runtime handle 必须绑定当前 attempt/claim；不能简单把这个稳定 execution ID 当成每次启动的新 ID，也不能为了采用 AX 改写文件或 round identity。

模型配置、审核工具、runner、策略与必要的隔离配置影响执行身份时，应具有可核对的 revision，并明确与已冻结 `executor_revision`、`semantic_engine_revision` 的关系。模型别名和配置 hash 不保证 provider 权重不可变或推理逐位复现。冻结版本不可运行时明确失败，不自动改用最新版本或较弱隔离配置。

PostgreSQL 继续拥有产品结果发布及 job/attempt 状态转换；backend 只报告观察事实和必要的恢复信息。确需持久化 remote handle 时，复用现有 `engine` ownership 和 store，不建设独立资源 registry、第二个产品队列或跨系统双写的产品状态机。

### 最小生命周期契约

需要保证的是语义闭环，而不是预先规定七个异步方法。当前有界同步调用可以继续使用 `run`、取消信号、progress callback、结果读取与清理；只有真实远程后端需要时才增加 create/watch/collect 一类接口。

| 生命周期 | 契约 |
|---|---|
| Prepare | 冻结输入授权与完整性校验；只准备本次所需文件，工具及依赖在构建阶段固定 |
| Start / ready | 识别实际 attempt 与 runtime handle；配置存在、运行环境就绪和工作完成是不同事实 |
| Execute / observe | 壁钟与模型预算有界；报告经过 schema 校验的进度，而非任意 guest 日志 |
| Collect / validate | 退出码和产物传输状态明确；schema、输入身份、coverage、evidence/locator 验证通过后才允许产品映射 |
| Cancel / destroy | lease 丢失、连接断开或超时后停止工作、撤销 grant，并有界回收整个执行域 |
| Recover / reconcile | 区分已验证结果、未执行、执行中和未知付费结果；读回/对账后恢复，不盲目重放 |

进程退出 0、AX `Running`、Pod Ready 或 workspace ready 均不等于审核成功。即使结果已生成，旧 attempt 也不能发布，清理失败也不能被描述为正常完成。无法确认撤权/回收时应隔离受影响执行域、拒绝不安全的新工作，并提供可操作的诊断。

恢复方案必须覆盖“外部调用已完成，但结果、用量或本地提交确认丢失”。数据库 fencing 只防止旧结果覆盖，不自动防止重复模型消费。系统必须明确不确定窗口和处理路径，不能声称分布式 exactly-once。

### 预算、能力与观察状态

预算由 Controller 可信配置决定，并明确总 deadline、输入/输出字节、CPU/内存、进程/文件、模型请求与 token 的作用域和执行方。优先复用现有 limits、`RuntimeConfig`、gateway grant 与 usage 数据，不再维护另一套默认值。重试、恢复或 backend 重连不得重新获得无限预算；对结果未知的模型调用，不得按未消费处理。

预算要区分声明与强制执行。现有 `prlimit` 不是整个进程树的 cgroup 总量隔离；选择新后端时必须验证实际执行域的资源限制及终止行为，而不只是检查 YAML 中存在 limits。Tenant 公平调度和并发配额只在真实多租户负载需要时增加，不把当前共享 UID 的串行执行简单改为并发。

复用当前 `reading/planning/checking/validating` 产品进度。基础设施 phase、readiness conditions、退出码、失败分类、清理结果和用量作为内部观察数据，不能替代业务状态或扩张公共 wire contract。只增加有消费者的 condition，并绑定当前 attempt，防止过期事件覆盖新观察值。

`PPAP_DEEP_CONTROLLER_SOCKET` 只表示 configured；不能借此宣称 live ready。Deep 不可用不得使 Standard/Core `/ready` 失败。日志与指标区分排队、启动、执行、校验和清理时间；日志按 tenant/round/job/attempt/handle 关联，指标避免把这些高基数字段作为标签。客户原文、完整模型轨迹及凭据不进入默认遥测。安全要求见 [Security](SECURITY.md#deep-runtime-的信任边界与演进要求)。

### 何时选择更强隔离，何时评估 AX

更强隔离由 threat model 驱动：不可信文档、模型生成代码或工具执行、跨租户数据边界本身就可能要求独立执行域，不必等到大规模并发。必须在目标内核/集群验证文件、进程、网络、凭据、资源及清理边界；只有镜像名称或 `runtimeClass` 不能证明隔离成立。

AX 则是另一项平台选型：只有需要跨节点调度、大量独立任务、不同工具环境，或有明确长任务恢复需求时，才比较当前方案、最小 Kubernetes 原生方案、直接 Substrate 与 AX 的收益和运维成本。先在 Issue 中确定代表性负载、评价指标与门槛，再运行实验；合法结论包括“不采用”或“延后”。不预先承诺 `AxRuntimeBackend` 一定成为终态。

若决定接 AX，需额外证明：

- PPAP 固定自有 runner/image/entrypoint；不在客户执行时进行开放式 goal bootstrap、任意依赖安装或 registry 发现。生产 debug 默认关闭。
- 模型访问继续经 PPAP gateway；provider key 不进入 Task env。跨 sandbox 时需要真实可达、受认证且按执行授权的 gateway，不能把当前 `127.0.0.1` 地址直接搬到另一节点，也不能通过公开 listener 绕过网络边界。
- Task 创建超时后可按绑定 identity 读回；异步状态和清理映射不破坏 claim fencing、结果校验与预算。只允许获授权且 revision 匹配的 adapter 启动执行。
- Runner 的命令结束、产物提交与 Task 生命周期分开处理；不能把 AX 的存活状态直接映射为 PPAP succeeded。
- Suspend/resume 不是已存在的 PPAP 产品语义。引入前须验证 checkpoint 范围、重新授权、预算延续、过期 lease 与外部调用恢复；工作目录快照不能冒充进程内存连续恢复。
- 在切换前通过后端契约测试、真实隔离测试、客户等价审核回归以及受控灰度/回滚验证；回滚只路由到满足冻结 revision 与安全要求的后端，否则明确失败，不回退 Standard。

这些验收可以并行，或由同一实现 PR 一起满足；不能为了 Issue 编号制造串行依赖。隔离提升也不以 AX adoption 为前置。

### 外部设计依据

参考 AX revision [`ac2332829f22360ff97b0ba34d94dd0dd782f17e`](https://github.com/google/ax/tree/ac2332829f22360ff97b0ba34d94dd0dd782f17e)，这些链接记录设计依据，不表示 PPAP 已安装或验证该平台：

- [README](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/README.md) 与 [DESIGN](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/DESIGN.md)：AX 是基于 Agent Substrate 的编排控制面，不是独立 sandbox runtime；文档明确提示仍有 breaking-change 风险。
- [Concepts](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/concepts.md)：参考 Task / Workspace / Model 的职责分离，不复制其资源 registry 或开放输入面。
- [Runner](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/runner.md)：该版本 runner 在命令退出后继续存活，控制面不直接读取命令退出状态；恢复持久工作目录时创建新进程树。
- [Roadmap](https://github.com/google/ax/blob/ac2332829f22360ff97b0ba34d94dd0dd782f17e/docs/roadmap.md)：预算、隔离配置、最小权限、身份和遥测相关规划不能当成已经验证的能力，实际接入时须重新核对源码和部署行为。

## 外部能力

飞书只负责身份、文件来源和必要的显式写回；Partner AI 负责外部权益/计费确认；LLM/OCR provider 是执行依赖；Deep Controller 是可选执行依赖。它们都是可选 capability，不成为整个 Core 的启动或普通 CI 前置。

#169 通知接入须区分可恢复的发送前准备与不可盲重试的外部发送。发送前必须以当前 claim/attempt 做数据库条件更新，持久记录已经进入发送边界；过期 Worker 不得继续发送。进入该边界后，超时或进程退出不能证明未送达，不能沿用普通审核任务的过期租约重跑规则。未知结果保留原意图，只有可靠回执或确认未发送的外部证据才能改变恢复资格。

任务通知复用 `engine.jobs/attempts`，数据库按租户、任务及节点唯一；待确认、开始审核和首次报告终结的通知意图与对应业务事务一起提交。普通审核 Worker 不领取通知，也不持有飞书发送凭据；API 进程领取后，在发送前重新检查创建人、租户、源文件权限和完成通知设置，再以当前 claim/attempt 持久化发送边界。发送前准备可接管；发送后未知不可接管。有效消息 ID 才能记录发送成功，不表示用户已阅读。尚无可靠外部证据时，未知状态保留，不能声称具备自动恢复或外部绝对去重保证。

ZIP 就绪与其通知意图同事务提交，按规范化打包工作去重，不受完成通知开关影响；失败打包不通知就绪，恢复成功后才产生意图。不同报告集合的通知互不覆盖。发送前重读该包及来源授权；消息链接固定 `packageRequest`，报告中心只读取该请求，不以最新打包结果替代。链接不可读不封锁已有 PDF，下载仍实时鉴权。

待确认卡片只有在完整列出文件、规则、移除原因与预计点数时才提供直接确认；截断内容或缺少回调校验配置时仅打开页面。发送边界同时固定该卡片的输入修订与确认请求。回调校验 Verification Token、应用及真实操作者，关联已确认发送的原消息，再通过同一个 `_accept_locked` 事务核对当前创建人、权限、规则版本并受理；不从按钮字段创建身份，不按新输入重绑旧卡片。网页先受理时只读回已有轮次，回执丢失重放仍使用原请求。发送未知、旧输入或过期规则均不授权新的审核；不声称支持加密回调、外部永久去重或飞书端响应时限保证。

[NotificationDispatch](models/NotificationDispatch.tla) 是这一并发边界的有限要求模型：一个通知意图、两个 Worker、两个接管版本，包含迟到发送与回执丢失。正向配置及 `-fence`、`-recovery` 反例分别检查所有者约束与重复送达。它不证明现有 Worker 已实现通知，也不保证最终送达；持久意图唯一性、消息内容/接收人授权和飞书读回能力仍须单独验证。模型不依赖飞书幂等保证，不改变现有 Worker 的凭据边界。

继续支持 `.doc` 时保留必要的受限转换边界。其它兼容层只有在存在当前消费者时保留。

交付镜像安装 LibreOffice Writer 与中文字体，并编译现有网络限制 helper。旧 DOC 审核和规则解析在 Worker 中复用受限转换；没有运行时则明确失败，不绕过沙箱。

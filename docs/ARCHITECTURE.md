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

Worker 在一个边界按冻结的 `analysis_mode` 路由：`standard` 走本仓 in-repo engine；`deep` 只通过 bounded UDS 调用独立 Controller。Deep 失败时写回结构化 failure 并将 job/round 标为 failed，不伪造业务判定，**不得静默回退 Standard**。Controller 协议 `extra=forbid`，不接受 host path、command、mount、image 或 env；Worker 不持有 Docker socket。`PPAP_DEEP_CONTROLLER_SOCKET` 只表示 Deep capability 是否配置；Core `/ready` 仍只探 PostgreSQL 与对象存储。

Controller 进程是 `python -m ppap_deep`。生产工厂要求显式设置 `PPAP_DEEP_RUNTIME=container-only`，并将 `PPAP_DEEP_SANDBOX_PROFILE` 设为 `off` 或 `ppap`，没有默认 process/checker 后端。`SemanticRuntime` 消费冻结的有效规则和文件版本，通过 `ContainerRuntime` 与可信模型 gateway 执行；guest 命令、模型 endpoint 和 provider key 不来自 Worker RPC。`off` 是降权容器执行，不保证独立文件系统或 egress 隔离，不能称为 gVisor/VM。具体运行与限制见 [Deep 部署说明](../deploy/deep/README.md)；启动或 UDS 可用不证明审核质量或强隔离。

规则检查、模型推断和文档抽取可以替换实现，但不能改变产品输入版本、授权和结果语义。只有真实当前需求证明价值时才新增服务、框架或隔离层。

Deep 的受理端和执行端以 `ppap_core.deep_policy.DeepExecutionPolicy` 共用实际配置身份，不只比较同名版本标签。它是纯配置值，不启动 Controller、不存密钥、不增加策略 registry；实际 `RuntimeConfig` 继承相同字段及摘要计算。新受理在现有 Round/job 事务固定摘要，执行时由真实 runtime 再核对；旧策略不可用时明确失败，显式新轮次才采用新策略。Standard 不计算 Deep 策略，历史读取也不依赖当前配置。

## 外部能力

飞书只负责身份、文件来源和必要的显式写回；Partner AI 负责外部权益/计费确认；LLM/OCR provider 是执行依赖；Deep Controller 是可选执行依赖。它们都是可选 capability，不成为整个 Core 的启动或普通 CI 前置。

#169 通知接入须区分可恢复的发送前准备与不可盲重试的外部发送。发送前必须以当前 claim/attempt 做数据库条件更新，持久记录已经进入发送边界；过期 Worker 不得继续发送。进入该边界后，超时或进程退出不能证明未送达，不能沿用普通审核任务的过期租约重跑规则。未知结果保留原意图，只有可靠回执或确认未发送的外部证据才能改变恢复资格。

任务通知复用 `engine.jobs/attempts`，数据库按租户、任务及节点唯一；待确认、开始审核和首次报告终结的通知意图与对应业务事务一起提交。普通审核 Worker 不领取通知，也不持有飞书发送凭据；API 进程领取后，在发送前重新检查创建人、租户、源文件权限和完成通知设置，再以当前 claim/attempt 持久化发送边界。发送前准备可接管；发送后未知不可接管。有效消息 ID 才能记录发送成功，不表示用户已阅读。尚无可靠外部证据时，未知状态保留，不能声称具备自动恢复或外部绝对去重保证。

ZIP 就绪与其通知意图同事务提交，按规范化打包工作去重，不受完成通知开关影响；失败打包不通知就绪，恢复成功后才产生意图。不同报告集合的通知互不覆盖。发送前重读该包及来源授权；消息链接固定 `packageRequest`，报告中心只读取该请求，不以最新打包结果替代。链接不可读不封锁已有 PDF，下载仍实时鉴权。

待确认卡片只有在完整列出文件、规则、移除原因与预计点数时才提供直接确认；截断内容或缺少回调校验配置时仅打开页面。发送边界同时固定该卡片的输入修订与确认请求。回调校验 Verification Token、应用及真实操作者，关联已确认发送的原消息，再通过同一个 `_accept_locked` 事务核对当前创建人、权限、规则版本并受理；不从按钮字段创建身份，不按新输入重绑旧卡片。网页先受理时只读回已有轮次，回执丢失重放仍使用原请求。发送未知、旧输入或过期规则均不授权新的审核；不声称支持加密回调、外部永久去重或飞书端响应时限保证。

[NotificationDispatch](models/NotificationDispatch.tla) 是这一并发边界的有限要求模型：一个通知意图、两个 Worker、两个接管版本，包含迟到发送与回执丢失。正向配置及 `-fence`、`-recovery` 反例分别检查所有者约束与重复送达。它不证明现有 Worker 已实现通知，也不保证最终送达；持久意图唯一性、消息内容/接收人授权和飞书读回能力仍须单独验证。模型不依赖飞书幂等保证，不改变现有 Worker 的凭据边界。

继续支持 `.doc` 时保留必要的受限转换边界。其它兼容层只有在存在当前消费者时保留。

交付镜像安装 LibreOffice Writer 与中文字体，并编译现有网络限制 helper。旧 DOC 审核和规则解析在 Worker 中复用受限转换；没有运行时则明确失败，不绕过沙箱。

# 安全边界

项目资产的默认使用规则由 [AGENTS.md](../AGENTS.md) 拥有。本文件只描述运行时信任边界。

项目资产可用于迁移/开发，不等于可以泄露 Secret、绕过租户/对象授权或忽略真实外部写操作的业务影响。

## 授权

API、Worker 和迁移操作使用各自需要的最小运行权限。业务访问始终检查租户和对象权限；外部身份使用 provider/app/tenant/subject 绑定。

登录不等于对象权限，也不等于人工复核权限。

模板入口与模板反馈只打开固定飞书文档及其联系章节，由飞书检查文档和附件访问权限。外链隔离 opener，不代理模板文件、发送反馈或修改协作者权限。历史内部 `template` 资源继续保留，普通文件授权仍拒绝读取，不能因专用接口取消而扩大历史数据访问。

报告分享冻结已授权的报告引用、产物和内容摘要。分享入口每次核对当前飞书应用、会话主体及组织，仅供该组织内已登录成员读取这些固定报告；链接本身不是授权，也不授予私有任务或原件权限。发送前重新核对人员状态或内部群的组织归属；缺少可验证的目录字段时不发送，不自动扩大应用权限。

## 文件与不可信输入

每个产品文件版本关联自己的存储对象/版本引用；后续上传不能重定向已引用历史版本。

用户可影响的 URL 导入必须限制协议、目标网络、重定向、响应大小和时间，避免 SSRF。文件扩展名不能作为内容安全判断。

文档内容是数据，不是授权。模型或解析器不能因为文档中的文本而获得工具、网络或无关凭据访问。

## Secret

Secret 包括 API key、app secret、verification token、session/cookie 和生产数据库/对象存储凭据。

已有组织内 Secret 可以用于授权的开发/迁移路径，但只通过 gitignored 本地配置、GitHub Secrets 或部署 Secret 注入；日志不输出 token、cookie、signed URL 或客户原文。

self-hosted InsForge Custom Compute 需要把宿主机 `docker.sock` 挂进 InsForge 容器。该 socket 等价于 host root；这是明确的平台决策，不是默认。InsForge 管理面必须保持访问受限。

## 外部效果

只有路径真正产生外部效果时才考虑幂等、未知结果和 readback。未知的非幂等结果不能盲目重试。

报告分享使用现有持久发送逻辑，在现有 `engine.jobs` 保存原请求与逐目标尝试；只有明确失败的目标可显式重试，成功目标不重发。物理数据库连接上的会话锁覆盖准备与发送，连接失效后旧处理器不能重新取得发送权。已越过持久发送边界的超时或中断保持未知；飞书一小时消息去重不等于长期 exactly-once，未接入按原发送 UUID 安全核对的能力。准备阶段的私有文件上传可能残留，不声称已经远程清理或撤回。

LLM、飞书、Partner AI 和其它可访问远程环境可以直接用于相关开发/验证；它们的临时不可用不阻塞无关 Core 路径。

## Deep runtime 的信任边界与演进要求

当前实现与配置以 [runtime.py](../packages/ppap_deep/src/ppap_deep/runtime.py)、[container.py](../packages/ppap_deep/src/ppap_deep/isolation/container.py) 和 [部署说明](../deploy/deep/README.md) 为准。`container-only` 在可信 Linux 容器内降权启动 guest，并清理环境、capabilities 和继承描述符；在 `sandbox_profile=off` 下不保证对恶意 guest 的独立文件系统或网络隔离。不能把降权、进程资源限制或外部容器的存在称为 per-execution gVisor/VM 隔离，也不能因 model key 未进入 guest 就宣称不存在数据外传风险。

以下是后端演进与上线必须验证的要求，不是对当前部署能力的额外保证：

- Worker → Controller 保持有界业务协议、租户/对象授权与冻结版本校验。执行配置只由可信 Controller 选择；文档、模型输出和 Worker RPC 不能授予任意命令、路径、挂载、镜像、环境或调试能力。Worker 不获得 Docker socket、Kubernetes 管理凭据或 hypervisor 权限。
- 不可信 guest 仅获得本次运行输入、必要工作空间和有预算的短期模型 capability；provider key、产品数据库与对象存储管理凭据留在可信边界。将 gateway 移到另一执行域时须校验调用身份、授权范围、过期/撤销和网络出口，不以暴露 loopback 服务替代这些边界。
- 更强隔离后端须在目标部署上验证跨执行/跨租户文件与进程不可达、禁止出口（包括云 metadata 与控制面）、只读输入、资源总量限制及子进程回收。共享 UID、网络和文件系统的串行 guest 不能仅通过增加并发宣称已隔离。
- lease 丢失、超时、Controller 崩溃和结果提交竞态都要保持 fencing 与撤权语义。无法确认模型消费、grant 撤销或 sandbox 清理时 fail closed；恢复先读回/对账，不盲目重放付费请求。
- guest 输出始终是不可信输入；产物读取须限制大小、文件类型与符号链接，验证 schema、冻结输入身份、coverage 和 evidence/locator 后才发布。日志默认只留有界、脱敏的执行事实；交互调试须显式授权、限时且可审计，不默认保存客户原文或完整模型轨迹。

架构分层、状态 authority 与条件性 AX 评估见 [Architecture](ARCHITECTURE.md#deep-执行环境的演进约束提议)。更强隔离可以由安全需求独立触发，不以引入 AX 或扩大量级为前提。

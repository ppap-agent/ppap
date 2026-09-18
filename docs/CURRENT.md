# 当前事实与读取边界

调查日：2026-09-18。以下是本轮通过 GitHub 读取的提交，不是声称对应环境正在运行这些版本。未来改动只复核相关来源，不默认全仓重读。

## 固定来源

| 仓库 | 分支 | 本轮观察的提交 |
|---|---|---|
| ppap | main | [a40fb11a4e762b6dac8392d7f58be4e6b194fe25](https://github.com/ppap-agent/ppap/tree/a40fb11a4e762b6dac8392d7f58be4e6b194fe25) |
| ppap-agent | main | [53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9](https://github.com/ppap-agent/ppap-agent/tree/53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9) |
| ppap-biz | ppap-feishu-2026-4-13 | [7570d966f178cde9c841600be485c43109d92019](https://github.com/ppap-agent/ppap-biz/tree/7570d966f178cde9c841600be485c43109d92019) |
| ppap-uac | master | [fbe912faf1894fe018ea88c20e46a57842468623](https://github.com/ppap-agent/ppap-uac/tree/fbe912faf1894fe018ea88c20e46a57842468623) |
| ppap-foundation | ppap-feishu-2026-4-13 | [5aee03b9f4c17520e5344b2fc4c11401ef0d066a](https://github.com/ppap-agent/ppap-foundation/tree/5aee03b9f4c17520e5344b2fc4c11401ef0d066a) |
| ppap-web | master | [557a2aeab3e5f5e68d8e8cf47593ca172da733d7](https://github.com/ppap-agent/ppap-web/tree/557a2aeab3e5f5e68d8e8cf47593ca172da733d7) |
| ppap-host | main | [c32b46a0a0d4f252a1677e8abce53a210b3c6d28](https://github.com/ppap-agent/ppap-host/tree/c32b46a0a0d4f252a1677e8abce53a210b3c6d28) |
| hax | main | [0923f5acea380ddd09813621703777153a8c7971](https://github.com/ppap-agent/hax/tree/0923f5acea380ddd09813621703777153a8c7971) |

开始调查时 ppap 只有 15 个文档/占位文件，尚无 Product API、Web、Worker 实现和开发 Issues；仅 PR #1 已合并。本轮新增文档与验证工具不改变应用未实现的事实。实施状态以后在真实 Issues 更新，不反复改写本调查快照。

## 已确认的源码事实

- [Java AuditFile](https://github.com/ppap-agent/ppap-biz/blob/7570d966f178cde9c841600be485c43109d92019/file-audit/file-audit-biz/src/main/java/com/icoastline/fileaudit/entity/AuditFile.java) 保存企业、文件引用、最近结果和规则等产品记录。
- [UAC 提交记录](https://github.com/ppap-agent/ppap-uac/blob/fbe912faf1894fe018ea88c20e46a57842468623/uac/uac-biz/src/main/java/com/icoastline/uac/entity/ThFeishuDocAuditSubmit.java) 又保存来源文件、导出、组任务与结果关联。
- [Python audit_files](https://github.com/ppap-agent/ppap-agent/blob/53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9/packages/ppap_db/src/ppap_db/models/audits.py) 保存 URL、内容、匹配规则和状态；[audit_tasks](https://github.com/ppap-agent/ppap-agent/blob/53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9/packages/ppap_db/src/ppap_db/models/tasks.py) 保存执行快照与 claim 信息。独立记录已证实，原件实际重复多少字节未证实。
- [旧任务编排](https://github.com/ppap-agent/ppap-agent/blob/53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9/ppap_agent/app/services/orchestrator/task_orchestrator.py#L250-L402) 对合法的重复 taskId 重置结果、尝试次数和 claim；它不是新产品轮次的幂等契约。
- [host 初始 ledger migration](https://github.com/ppap-agent/ppap-host/blob/c32b46a0a0d4f252a1677e8abce53a210b3c6d28/db/migrations/20260828_ppap_ledger.sql) 定义另一套 documents/jobs/findings；[startAudit](https://github.com/ppap-agent/ppap-host/blob/c32b46a0a0d4f252a1677e8abce53a210b3c6d28/src/lib/startAudit.ts) 在客户端调用 Function 后写 usage。该文件不能证明真实计费已接通。
- [foundation pom](https://github.com/ppap-agent/ppap-foundation/blob/5aee03b9f4c17520e5344b2fc4c11401ef0d066a/pom.xml) 是共享平台，声明 Java 11 / Spring Boot 2.5.6；不能用其它 README 的 2.4.1 概括全部 Java 仓。
- [web package](https://github.com/ppap-agent/ppap-web/blob/557a2aeab3e5f5e68d8e8cf47593ca172da733d7/package.json) 包含 Vue、Webpack 双入口和 opdev；未声明独立 test script。这不证明其它环境没有测试。
- [引擎测试指南](https://github.com/ppap-agent/ppap-agent/blob/53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9/ppap_agent/tests/README.md) 指向 V2、POC 分组、跨格式测试和人工 oracle；文档报告路径是 manual_linegrid → pymupdf_find_tables → OCR，不能笼统称所有 PDF 路径 OCR-first。本轮尚未运行这套 corpus。
- [lark_adaptor](https://github.com/ppap-agent/ppap-agent/blob/53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9/lark_adaptor/README.md) 是可选会话/群聊流程，复用同一审核执行路径，并非另一个独立审核算法。

## 需要纠正的权威冲突

[hax 产品页](https://github.com/ppap-agent/hax/blob/0923f5acea380ddd09813621703777153a8c7971/products/ppap/README.md) 和 [V2 计划](https://github.com/ppap-agent/hax/blob/0923f5acea380ddd09813621703777153a8c7971/products/ppap/feishu-native-v2-plan.md) 仍包含“最终 ppap-host”和本地实施记录。最新用户决定已将终态改为本仓；这些资料继续证明旧契约和历史，不拥有新架构。未提交/不可读取的工作不能算目标能力已完成。

[hax 用量记录](https://github.com/ppap-agent/hax/blob/0923f5acea380ddd09813621703777153a8c7971/runtime/tke-cls-6e71r2cz/PPAP-VOLUME.md) 是 2026-08-31 的读取：付费统计取已确认且非试用行。保留这一迁移语义，不把旧余额或默认 500 点当当前政策。

## 尚未证实

目标隔离部署、真实凭据、当前生产镜像与数据库状态、所有消费者、完整源码权属、样本发布权、代表性格式/规则覆盖和后续本地工作，均不能从本轮源码读取推断完成。

本次执行沙箱无项目 checkout/开发凭据，直接出站请求遇到 DNS 失败；GitHub 连接器读取与写入仍可用。未访问客户数据库或复制秘密，未证明目标产品 Live。后续必须在提供的隔离部署重跑相应 Gates；记录实际版本、时间和测试身份范围。

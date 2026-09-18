# 能力与数据归属清单

证据基线：[CURRENT](CURRENT.md)。以下是目标处置，不是已完成迁移。旧系统消费者未确认时，DELETE 只表示新产品不纳入，不能据此停服。

| 能力 | 当前来源与证据 | 目标处置/拥有者 | 必要证明与退出条件 |
|---|---|---|---|
| C01 身份、租户、协作授权 | UAC submit 的 openId/tenantKey；hax V2 A01/A14 | PORT：Product API 最小用户/成员/身份模型，框架可选 | 真实登录回原对象，跨租户拒绝；不按邮箱猜身份，不无声丢企业协作 |
| C02 文件和历史原件 | Java AuditFile、UAC source、Python audit_files、host documents | MERGE：product 文件版本 + 对象存储 | 同名不同内容分版，历史下载校验一致；迁移旧引用后才退旧文件入口 |
| C03 资料集与审核草稿 | hax 术语；引擎 context_only/group | MERGE：product 范围、目标和参考角色 | 未受理不检查；参考被使用不显示完整通过；固定版本集合 |
| C04 识别、规则匹配与规则版本 | TaskOrchestrator 校验 matched_rule_id/project、文件名和 checker 数 | MOVE/改造：Worker 计算，product 选择和版本契约 | 真实匹配/人工选择，禁止截断规则、静默换规则；产品规则 ID 不冒充 engine ID |
| C05 受理、执行与恢复 | 旧 POST tasks 重置；audit_tasks claim/snapshots | MERGE/MOVE：产品轮次 + engine 尝试 | 并发幂等、断线回访、过期 worker 拒绝、未知远端提交先对账 |
| C06 检查器、DocOps/OCR/PDF/Vision | 引擎 V2/POC/PSW 跨格式测试指南 | MOVE 或证据驱动替换：本仓 packages | 同一有权使用 corpus 和人工 oracle；允许差异单独审阅，缺检查不能 PASS |
| C07 结果、覆盖与证据 | 多端 result JSON；hax A06-A11 | MERGE：product 结果版本/检查项/原文位置 | 页面读真实结果，定位正确原件；空结果、ERR、缺 locator 明示 |
| C08 人工复核与重审 | hax 术语、A17-A18；目标尚无实现 | 新建最小产品能力，不移植审批中心 | 有权限者留理由，并发冲突可见；机器事实不改，新轮不继承旧批准 |
| C09 Web/PWA 和飞书入口 | web Vue 双入口；host PC 迁移；hax旧链接契约 | MOVE/重构或验证替换：apps/web | 一份事实，刷新/深链/窄屏/键盘可用；旧入口迁移有消费者证据 |
| C10 飞书 Drive/Docs 与显式写回 | UAC submit 导出状态；hax V2 完整承接要求 | PORT/ADAPTER：Product API 渠道适配 | 真来源权限、不可变导入；不把读结果实现为写评论；保留必要旧 Docs 能力 |
| C11 用量和 Partner AI | hax PPAP-VOLUME；host 客户端 insertUsage | MERGE/ADAPTER：本地事件 + 外部确认 | 试用/待上报/确认/未知分开；同轮不重复收费，不造余额 |
| C12 历史映射与迁移 | UAC/Java/Agent/host 各自 ID；hax 现场记录 | 显式迁移工具，不加永久第二台账 | 归属、数量、字节、关联、旧链接和新增写入核对；缺历史快照披露 |
| C13 部署、备份与观察 | 引擎运行方式；hax runtime 手册 | 新产品部署/恢复入口 | 新环境可重建；数据库+对象同时恢复；任务可继续；无秘密日志 |
| C14 Java gateway/platform 通用能力 | foundation 模块列表 | 只迁 PPAP 必要行为；共享系统 UNKNOWN | 消费者清点，不能停掉非 PPAP 产品；普通代理不保留业务 UAC |
| C15 lark_adaptor 群聊工作流 | 独立可选会话 API 和 Redis | 新主线 DELETE；必要库能力可提炼 | 未知调用方先核验，不把它的群聊能力计入现有飞书验收 |
| C16 发布与贡献可接管性 | 目标仓未有应用/分发流程 | 新建工程交付能力 | 全新环境、锁定依赖、CI、许可证/样本权利、升级/恢复文档和支持范围 |

## 当前信息流与收敛

```text
旧：Web/飞书 -> UAC/平台 -> file-audit -> transformer -> Python engine
                   |           |                         |
               submit记录    文件/最近结果             audit_files/tasks
另：host -> Function/PostgREST -> documents/jobs/findings
另：lark会话 -> 同一 Python 检查能力

新：Web -> Product API -> 固定输入/规则/轮次 + durable工作 -> Worker
                        -> 对象版本                 -> 结果/证据
```

以上旧链是源码和文档所支持的角色图，不是本轮抓包。实际生产调用比例和故障链仍需运行证据。

## 不应删除的“重复”

源文件、OCR 文本、转换工作簿和引擎输入快照不是相同数据。保留有明确生产者、来源版本和重建规则的派生物；去除的是多套竞争产品事实。备份、审计历史也不属于可随意去重的账本。

## 遗留读取最短路径

改受理先读 CURRENT 中 TaskOrchestrator；改文档先读三个文件模型及原件测试；改权限先读 hax A01/A14 和实际鉴权实现；改收费先读已注明日期的用量记录与 provider 契约。其它旧产品只在当前消费者证据要求时展开。

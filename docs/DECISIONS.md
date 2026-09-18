# 决策边界

接受日期：2026-09-18。产品语义只在 [Northstar](NORTHSTAR.md)，本文件保留取舍与重新评估条件。

| 决定 | 原因 | 重新评估条件 |
|---|---|---|
| 一个最终产品仓，模块化 API + 异步 Worker | 旧公司边界不是领域边界；共享交付和契约，保留长任务隔离 | 独立扩缩容、安全或团队责任出现可验证需要，不凭框架默认拆服务 |
| 一个 PG 数据库，product/engine 分责 | 可原子受理与入队；产品身份不被引擎重试改写 | 数据主权/负载确实不能满足时再 ADR，不预先加 broker |
| 原件版本与机器/人工记录分离 | 历史证据不能随当前云盘或重试变化 | 保留/删除政策须明确，不静默破坏既有证据链 |
| 引擎迁入，不保留永久旧 HTTP 产品层 | 消除跨仓产品台账与重复生命周期 | 过渡适配必须绑定退出证据，不等于永久复用许可 |
| 飞书与 Partner AI 作为适配器 | 同产品、不同来源与外部确认；避免重建 UAC | 新渠道必须复用产品对象而非产生另一套事实 |
| Core LLM 使用 OpenAI-compatible 边界，canonical 配置为 `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL` | 不绑定模型厂商；Base URL 与模型身份必须显式，只有 API key 是 Secret | 出现第二种真实且不兼容的协议后再引入 provider abstraction，不预建 vendor 层 |
| Core 开发只要求 LLM Secret；基础设施有隔离 dev defaults，可选 integration 懒启用 | 降低 Cloud Agent/本地开发上下文，并验证 Feishu/Partner AI/legacy 真的是 adapter | 某外部能力被产品决定提升为 core invariant 时重新评估；不得仅因旧实现依赖而提升 |
| 不迁入 lark_adaptor 独立工作流 | 它服务群聊实验，当前产品已排除主路径 | 可提取直接有消费者的能力；未知调用方未清点前不停止旧部署 |
| 工程质量不等于公开发布许可 | 遗留源码、规则与客户样本权属尚未核实 | 权属、许可证、脱敏与分发审批完成后才公开 |

## 技术选择的状态

[RESEARCH](RESEARCH.md) 给出当前推荐和候选，不把技术栈变成产品政策。

建议先复用 Vue UI 与 Python 审核资产，以 Vite/FastAPI 等成熟工具建立最小正常路径；不建议现在额外采用通用 Agent/workflow 平台。理由是已有可用资产和较小的状态问题，而不是证明其它框架较差。

这只是设计判断，尚无本轮 PPAP 性能比较。文档抽取替换、队列框架和身份方案通过同一 corpus/故障场景验证后再定具体版本。不得把“模型输出 schema 合法”当作“审核判断正确”。

配置同样遵守一个事实一个 authority：应用代码只认 canonical `LLM_*` 名称；已有 Cursor/旧环境的 `OPENAI_API_KEY / OPENAI_BASE_URL / MODEL_NAME` 只做一次环境侧改名/映射，不形成长期兼容层。

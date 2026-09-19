# 迁移来源清单

`ppap-agent` GitHub 组织中的仓库、配置、文档、测试数据和我们可访问的远程环境，默认都属于本项目可直接利用的迁移资产，除非之后明确排除。

本页只帮助快速找到现成能力，不是权限审批清单。

| 能力 | 固定来源 / baseline | 迁移方向 |
|---|---|---|
| 审核引擎、规则、测试、DocOps/PDF/OCR/Vision | `ppap-agent@53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9` | #3 整体迁入当前 PPAP 需要的资产，保持测试结果 parity |
| 飞书前端 | `ppap-web@557a2aeab3e5f5e68d8e8cf47593ca172da733d7` | #11 迁入/现代化，现有客户任务不减少 |
| 飞书身份、提交、用量 | `ppap-uac@fbe912faf1894fe018ea88c20e46a57842468623` | #11 收敛到 Product API / 同一租户与 usage 模型 |
| Java PPAP/file-audit/transformer | `ppap-biz@7570d966f178cde9c841600be485c43109d92019` | #11 保持当前客户能力，终态不保留 PPAP Java 产品链 |
| Host slice | `ppap-host@c32b46a0a0d4f252a1677e8abce53a210b3c6d28` | 可复用已工作的 Web/hosting 资产，不保留第二套台账 |
| 产品行为与现网运行知识 | `hax@0923f5acea380ddd09813621703777153a8c7971` | 作为迁移下限和现网操作来源，不成为终态 runtime |
| 当前远程环境/数据源 | 组织内现有可访问环境 | 直接用于开发、集成、切换和按需数据操作 |

当前不建设通用历史审核迁移：试用用户可重新导入文件和规则；未来有具体客户需求时按实际数据处理。

迁入后本仓拥有当前代码、测试、配置和运行入口；来源仓只保留历史价值或其它非 PPAP 消费者。

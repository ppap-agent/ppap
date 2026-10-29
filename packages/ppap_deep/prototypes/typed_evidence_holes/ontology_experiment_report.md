# Ontology：反例、真实模型与离线重放

本次验证关系含义、对象粒度依据和文档支持的局部解释修订。实现继续使用 RulePlan、Binding、Typed Evidence Hole 和同一个宿主求值器。业务输入只有明确的 rules + documents。

自动验收：11 项受控案例的概念、绑定和最终判断均通过。与最终源码逐字节一致的隔离候选通过 787 项测试，保留 1 项已有预期失败；合入主目录后 53 项相关测试通过。另有 43 项应用反馈集成测试通过，Ruff 与 Mypy 通过；这些测试数量有范围重叠，不相加为独立样本数。

完整原件副本、模型提案、事件和派生记录保留在本机的 `output/`，该目录被 Git 忽略。本文的 `output/` 相对链接只供本机核对；远程提交只包含源码、测试和这份简明报告，不包含这些完整实验记录。

## 版本与证据范围

| 记录 | 实际执行 | 能证明的范围 |
| --- | --- | --- |
| [v7 九项 pilot](output/ontology-v7-live-01/report.json) | `3887e9a`；Grok-build 1.0.70；grok-4.7／medium | 从原始 DOCX 规则和 XLSX 形成计划、绑定及宿主结果的构造实验 |
| [v7 V3 Aries](output/ontology-v7-v3-aries-01/report.json) | 同一候选和模型；1 个目标、9 条规则、2 份上下文 | 真实 PFD 规则和原件的选择复核，保留完整 corpus 分母 |
| [v8 metadata 正例尝试](output/ontology-v8-focused-01/report.json) | 隔离的 point-binding 修订候选，中性目录 | 编译阶段第 19 请求读取中断，未得到可验收结果 |
| [v8 metadata 空名称负例](output/ontology-v8-focused-02/report.json) | main 的 point-binding 候选，中性目录、另一明确输入 | 完成；概念、绑定和预期判断各 1/1，NG |
| [最终候选离线重放](output/ontology-v8-replay-02/report.json) | 已审源码指纹 `dbbbed43…`；新增模型调用 0 | 固定旧提案在当前求值器下的派生结果与修订评分 |

v7 pilot 的目录名含 `matches`、`differs`、`unresolved` 等 case key，且 LocalGrokRunner 提示包含输入路径。这给模型提供了预期线索，因此它**不是完全盲测**。规则、原件和模型可读 stage 中没有直接注入预期计划或最终 findings，但路径提示仍限制结论。后续入口改用 `unit-<sha256>` 中性目录，回归检查实际 prompt 和 stage 可读内容；旧记录保持原样。

v8 metadata 的 focused-01 使用隔离候选，focused-02 从 main 的 point-binding 候选运行。两者使用的 trace 文件内容版本为 `48506989`；最后的 collection 局部待确认含义守卫小修为 `015aec27`，由定向测试、全套回归和离线重放验证，核心修订提交为 `c5561b9`。不能将这些 metadata 调用写成最终源码已完整真实实测。离线重放也不构成新的模型实验。

## 九项构造反例

| 样本 | 手写预期 | v7 实际结果 |
| --- | --- | --- |
| 同码不同产品，存在不对应工序 | NG | NG |
| 同分类、不同工序 | NG | NG |
| 跨行同对象，保留分类冲突 | NG | NG |
| 重复对象不得合并 | NG | NG |
| 零件通过外键关联本次文件实体 | PASS | PASS |
| 字面拼接对应 | PASS | PASS |
| 字面拼接不对应 | NG | NG |
| 作业单元含义未确定 | UNABLE | UNABLE，并有一项澄清 |
| 两个产品各自的 010 均正确对应 | PASS | PASS |

这些构造例的最终状态与手写预期一致 9/9。它们不证明任意布局或真实业务准确率。所有真实提案的 `plan_refinements` 均为空；自动使用文档支持的计划修订仍只在受控反例中证明，包括 code-only 的 false NG 经复合身份修订成为 PASS。

原评分曾将概念记为 4/9、绑定记为 8/9。只读复核发现三类 oracle 过严，已修评分并保留旧报告：

- 文件候选可以包含本次其他授权文件；规则只要求目标附件存在，不能强制候选集合恰好为一个文件。
- 字面拼接规则未规定源记录的完整身份集合。`code` 与 `code+name` 都可保留合法记录；仍严格核对拼接字段、顺序、标点、来源和完整对象范围。
- 预期的语义澄清可将目标身份保留为 `field_holes.unresolved`，而不是提前认定原列含义。只有同身份字段、实际确认问题、正确表头证据和明确未知结果相符时才接受；错列、无问题和源侧漏绑仍拒绝。

当前重放评分为：严格概念合同 7/9、绑定 9/9、最终预期 9/9。其余两项是关系 kind 的解释差异：模型将同编号记录的对应表示为 `association`，gold 采用 `same_instance`；实际匹配键、一一对应与重复检查仍正确。它们保留为未裁定的建模差异，不据标签直接宣称业务错误，也不放宽所有 association 的身份校验。

## 真实 V3：6/9 标签一致

Aries PFD 的实际结果为 `[NG, NG, NG, PASS, NG, PASS, PASS, UNABLE, NG]`。相对原 oracle 差异在 R2、R3、R8；仅选择了 1 个目标，另有 26 份文件、211 条规则未执行。**6/9 是选择样本的标签一致率，不是全量准确率。**

- **R2/R3：规则解释。** 程序要求全部适用数据行的类别与描述都非空。排除标题和表末说明后有 79 行，只有 `PFC-SZ!K60`、`PFC-HF!K39` 类别非空（ISC），两行对应描述均非空。旧 oracle 的 PASS 与“只检查已标类别行”相容。R3 原文还混用了产品与过程字段。原规则和 oracle 均未改写来制造一致。
- **R8：空适用范围。** 91 个适用性问题都回答 false，工序名称没有明确返工或返修。UNABLE 来自已保存计划的 `empty_scope_status=UNABLE`，不是漏读、漏答或 Worker 失败。新的编译 guide 与旧计划政策须分别解释；离线重放不会重新编译旧计划。
- **表头表示。** 两个表头对象正确引用各自的 `B3/F3/K3/B4/K4/B5/F5/K5/B6`，但模型用 N–U 空白列占位，绕开原列聚合限制；`ColumnExists` 仍留下虚假未知。当前重放消除了该未知：R1 从 checked 2／unknown 1 变为 checked 3／unknown 0。最终 NG 保留，因为两张表的项目车型填写区为空，且 PFC-SZ 代码填写区也为空。

完整原件诊断见 [V3 diagnosis](output/ontology-v7-v3-aries-01/diagnosis.md)。

## 用量、失败与迭代

| 真实运行 | 已结算请求 | 输入 tokens，含缓存 | 输出 tokens | 工具调用 |
| --- | ---: | ---: | ---: | ---: |
| v7 九项 pilot | 452 | 19,792,007 | 303,909 | 639 |
| v7 V3 Aries | 93 | 7,473,972 | 102,772 | 113 |
| metadata 正例未完成尝试的已知下限 | 18／已发 19 | 580,148 | 11,050 | 未完成 |
| metadata 空名称负例 | 62 | 2,976,280 | 44,013 | 92 |

两个 v7 运行均完成，消费已结算；无 host 编译或审核 repair stage，0 compaction，上游重试配置为 0。这不表示没有迭代：pilot 有 15 次 terminal 非零退出，V3 有 6 次；它们属于 agent 本地命令或测试修正。V3 总耗时约 25.6 分钟，其中编译 37 请求、输入 2,550,636、输出 50,904；模型在两个 stage 都读取了较多宿主源码。按需布局读取本身不能证明低 token 成本。

metadata 正例第 19 请求在 `provider_read` 发生 `IncompleteRead`，供应商已返回 HTTP 200，消费未知。该请求不自动重发，其 token 不能记为零。后续负例是另一明确输入的独立实验，不是对未知消费请求的盲目重试；其结果以单独记录为准。

负例 focused-02 成功完成，耗时 678.472 秒，62 个请求全部结算。编译占 36 请求、2,070,607 输入、33,237 输出；审核占 26 请求、905,673 输入、10,776 输出。0 host repair、0 compaction；40 次 terminal 中有 2 次非零退出。输入字节未变，`uncertain/in_flight/budget_exhausted` 均为 false。

该提案使用两份纯点定位 Binding，`columns={}`、`field_holes={}`，没有空白辅助列占位。每份表单将第 2–3 行作为一个对象，身份取 B3，比较的名称取 B2；待审名称 B2 的已知空值保留，宿主得到 NG。最终源码对同一提案的离线重放仍为 NG。这个完成负例不能替代未完成的正例，也不能据 1 个完成样本宣称 metadata 通用准确率。

## 离线重放与记录

[replay-01](output/ontology-v8-replay-01/report.json) 保留小修前工作候选指纹 `8467e608…` 的派生记录。[replay-02](output/ontology-v8-replay-02/report.json) 使用最后的 `015aec27` trace，完整源码与 scorer 指纹为 `dbbbed4337da20ba83aeda87c3a49142c349557c14f650ca46310569c03383cd`。两次均只使用保存的原件、计划、绑定和答案，新增模型调用为 0；九项、V3 和已完成 metadata 负例的最终状态均未变化。它们不替代新版本的真实模型调用，也不改变原消费记录。

重放逐项核对输入字节 SHA、原件快照来源和原生重读一致性，保存 proposal 摘要并检查原报告、summary、输入和提案文件前后未变。相同读取器的重读不证明读取器本身无误。脚本保留于 ignored `.artifacts/ontology_v8_replay.py`，派生结果进入既有 `output/`，没有另建产品状态账本。

最终重放扫描 245 个派生、原记录及报告文件，配置 secret 命中、token 模式命中和隐藏推理字段命中均为 0；扫描只输出计数，不输出配置值。记录保持 `training_eligible=false`、`human_gold=false`；局部解释确认或状态一致不能自动生成整轮正确标签。

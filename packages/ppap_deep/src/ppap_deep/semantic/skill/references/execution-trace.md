# 执行证书

正式输出是 AuditTrace：bindings、uses、answers。宿主拥有计算、观察、计数、来源和最终报告。模型不输出 QueryProof、SemanticProof、逐行 PASS/NG 或汇总报告。

1. 首次审核先将 initial-execution.json 复制为 execution.json。宿主已按程序填好全部 use 身份和 unresolved 输入；这只是待补全候选。obligations.json 列出每条规则潜在使用的集合、字段和语义问题，文件级 fields 是搜索提示。该视图不增加审核义务，也不将不同规则的同名集合自动合并。需要逻辑细节时读取 plans.json 和冻结条款；结合 document-index.json 先看 layout 和表头，按需查看正文与视觉区域。修复阶段从 previous-execution.json 保留有效工作，不重置为初始候选。
2. 定义物理 Binding：原文 document_id、SHA、table、header_rows、完整 first/last_row、原始列映射。field_holes 区分 absent 和 unresolved，已确认缺列必须有可读的完整表头搜索范围。绑定不得增加全局 excluded_rows。
3. 一个逻辑对象跨行时，在对应 scope.objects 声明 id、完整 rows；需要一行多个对象时给出各对象 fields 的原始定位。逻辑 input 必须有 RulePlan.ontology 中的 grain、attributes 和 identity。宿主验证全部声明物理行都被对象覆盖；对象冲突值保留，不能靠挑行或去重消除。文件实体 input 可用 files/document_ids，宿主只取本次冻结文件的原生 id、format、sha256。
4. 每条 target/clause 提交一个 use，inputs 把程序中的逻辑集合映射到本轮文档。bound.scopes 引用 binding_id；每个 scope 独立声明 excluded_rows/unresolved_rows，附真实定位和原因。unresolved_scopes 保留尚未绑定的其他范围。已搜索而确认不存在用 absent，说明 searched_documents 和实际依据；未定位用 unresolved。
5. 每完成一处绑定、范围或答案修改，就保存 execution.json 并求值，未完成集合继续保持 unresolved。不要等全部探索结束才写候选。运行：

```sh
python -m ppap_deep.semantic.audit_tools evaluate execution.json --out evaluation.json
python -m ppap_deep.semantic.audit_tools view evaluation.json --offset 0 --limit 8
```

同一个 evaluator 读取 documents.json、plans.json、targets.json。完整计算写入 evaluation.json；标准输出只有计数与问题 ID。view 分页只改变展示，不改变计算范围。

6. 只回答宿主产生的 need_id。答案 value 为 true/false/unknown，附简短中文 reason、可用的决定性原文 citation 和实际未知 diagnostic。不要把审核结论写成答案。修改绑定、文件版本或程序后，旧 ID 失效；移除不再属于当前问题的答案。
7. 再次 evaluate，直到必要的知识问题得到回答或真实未知已明确。影响本条结论的缺回答、未定位集合和遗漏 use 产生 UNABLE，无需重写全部结果。非法来源、错 SHA、重复身份及过期答案是协议错误。初始候选或保存了部分工作不表示审核完成，也不自动提供中断后的恢复能力。

前置条件满足不会补出正文。未知不是空集合。已知违规可支持 NG，同时保留其他未知。缺少可选引用只形成证据提示，不能改变实际判定或单独触发模型修复；可用时引用决定性字段或行，表头只能支持表头事实。

关联集合的范围未知进入对应叶子的三值结果，再由 all/any/not 组合；不因无关分支未知给已确定的表达式增加义务。被遮蔽的未知仍保存在组件观察中。量化的源总体范围仍须完整，exists 已有命中但源范围未完仍为 UNABLE。

宿主读取或局部问题达到资源上限属于 RESOURCE_LIMIT，不能通过删列、删记录或改成未知来修复，也不触发审核结构修复。可选引用仍使用独立软预算。

标题、合并组行和明细用途不同。同一 physical Binding 可以用于不同集合；只有对应 use 的成员政策生效。原生数值不会因 int/float 实现差异变成不同键；文本编号和布尔值不自动转换。文件名、宽松子串及删标点只可作探索线索。

冻结条款和缓存程序仍可能被错误解释，绑定也可能漏表；宿主完整展开声明范围不等于独立证明业务范围正确。保持这些限制可见，不把自己的说明标记为人工确认或训练金标。

## Typed Evidence Hole 与解释确认

needs 使用同一 EvidenceHole 合同。boolean 保留原 true/false/unknown 答案；attribute 根据 ontology 给出 string/number 类型。事实答案为 kind=fact、need_id、input_id、value、origin=native/model、reason、evidence；未知用 state=unknown 且 value=null。宿主验证实际类型、请求身份及原件引用。模型解释与原生观察分别保存，不接受最终 verdict 或 counts。

字段含义确实需要用户决定时，在 Binding.interpretations 给出稳定 id、field、question、少量 candidates（id/label/explanation）和原件表头 evidence。当前仅冻结 appendix 的 question_id、candidate_id、content 与明确 document_ids/rule_ids 范围全部匹配时解除对应 gate；没有确认时保留 unknown_interpretation，宿主产生面向产品的 clarifications。候选、模型答案与历史聊天不能充当用户确认。

先看 layout-probes.json 的固定局部窗口及规则属性需求，再用 document_tools view 查看相关范围。宿主的完整原生 snapshot 不等于需要将全部 data 打印给模型；完整相关范围由程序求值。

对象范围存在有限候选时，scope.object_choices 声明每套 id/label/objects；不能同时声明已选 objects。宿主先验证每个候选的来源与全行覆盖，再提出 object_scope Hole。kind=fact 的 value 只选择给出的候选 ID，由同一 evaluator 重建逻辑对象；未答、拒绝或未知不会退回按物理行计数。

Related.key_match=evidence 只在需要有类型关系事实时使用。宿主提供已知 source/target 对象引用及固定方向，提出 relationship Hole；value 为 {source,target,exists}，端点必须属于此次请求。宿主随后比较原始字段，不把关系认定变成相等值，也不反向增加义务。

interpretation Hole 的 value 是候选 ID。requires_confirmation 默认为 true：模型选择只能产生带来源的推荐，不能代替用户确认。原规则已足够支持的普通解释可设为 false，经同一 typed 接口选择后保留 model 来源并参与求值；不能把它写成用户附录。删除或改变 ontology、附录、绑定后，旧 need 身份不再适用。

AuditEvaluation.readings 保留每条规则实际物化的属性、完整行范围和缓存复用。实体属性按程序需求和明确身份读取；原生 snapshot 仍保留完整原件观察。逻辑对象的多源属性和解释来源随观察输出，冲突值不覆盖。

事实引用还必须属于宿主请求的对象行、表和已明确的属性来源；同文件另一对象即使有相同数值，也不能借用其引用。native 答案核对原始标量类型和值，range 引用不把数值显示文本变成原生字符串。结构、关系和语义判断的解释不能标成原生标量。

产品确认的 scope.rule_targets 精确列出 {document_id: 审核目标文件版本, rule_id: 条款ID}。它与 scope.document_ids（正在解释的原件）分开：两个目标都引用同一 CP 且都有 R1 时，仅确认其中一个 pair 不能解除另一个目标的解释 gate。clarifications.scope 聚合实际受影响的 pairs；rule_targets 存在但为空或不匹配时不应用。仅旧版直接重放可省略它，沿用显式 rule_ids 的广播含义；新产品提交应始终带精确 pairs。

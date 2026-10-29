# 工具命令

优先复用已生成的 documents.json。读取工具只提供观察；正式计算及报告使用同一 evaluator。

```sh
python -m ppap_deep.semantic.document_tools view documents.json --document DOCUMENT_ID --table Sheet1 --rows 1:15 --columns A:F
python -m ppap_deep.semantic.document_tools view documents.json --document DOCUMENT_ID --pages 1:2
python -m ppap_deep.semantic.document_tools inspect inputs/*.xlsx rules/*.docx --out-dir scratch/snapshots
```

将 DOCUMENT_ID 换成 document-index.json 中的准确 ID。view 直接选择现有索引中的一份快照；单独快照文件也可使用，此时省略 --document。表格视图只显示请求行列及与该范围相交的合并区域；合并单元格同时显示真实 anchor、值和状态。PDF 指定 --table 时查看网格片段，使用 --pages 查看页面上下文。inspect 保存完整快照并返回短清单。段落、表、页、block 和行列均从 1 开始。规则来源要阅读完整条款和必要上下文，不能用默认预览代替完整规则。

## 正式求值

```sh
python -m ppap_deep.semantic.audit_tools evaluate execution.json --out evaluation.json
python -m ppap_deep.semantic.audit_tools view evaluation.json --offset 0 --limit 8
python -m ppap_deep.semantic.audit_tools view evaluation.json --need <question-id>
```

默认从工作目录读取 documents.json、plans.json、targets.json，可用同名参数指定路径。输出完整 report、observations 和 needs。标准输出只含数量及至多 8 个问题 ID；view 显示必要局部上下文。answers 只回答这些 ID，改绑定或版本后须重算。缺回答是未知，协议错误不是零差异。

## 可选探索

```sh
python -m ppap_deep.semantic.document_tools bind documents.json --binding binding.json --out scratch/bound.json
python -m ppap_deep.semantic.document_tools query --table records=scratch/bound.json --queries queries.json --out scratch/results.json
```

Binding 的唯一 schema 见 [执行证书](execution-trace.md)。bind 保留完整物理范围、父子表头路径、原始值、字段状态和 typed citation；排除行与未知行角色属于 use.scopes，不在 Binding 中。

SQL 表包含 row_id、原字段、states_json、evidence_json、scope_state。引用含中文、空格或引号的列时使用 SQL 双引号。SQLite 类型转换、NULL、INNER JOIN 和空集聚合不能替代审核的类型、完整源集合或未知合同。错误、过滤后的零行和未物化的查询都不是完成证据。辅助结果不改变缓存程序，也不成为第二份报告。

## 局部字段观察

```sh
python -m ppap_deep.semantic.checker_tools documents/target.json --request checks.json --out local_checks.json
```

checks.json 包含同一 physical binding、checks（field_nonempty 或 numeric_at_least），以及可选的同一 BindingScope。此工具调用同一个 evaluator，返回完整违规/未知行号和至多 64 个样例。文本数字不强转为数值，未知公式缓存保持未知。局部条件通过不等于完整客户规则通过。

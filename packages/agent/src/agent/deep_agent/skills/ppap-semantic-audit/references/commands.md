# 工具命令

读取工具只提供观察，帮助编写检查程序；正式结果只来自固定 checker 的执行。documents.json 由下面的 inspect 生成。

```sh
python -m audit.document_tools view documents.json --document DOCUMENT_ID --table Sheet1 --rows 1:15 --columns A:F
python -m audit.document_tools view documents.json --document DOCUMENT_ID --table Sheet1 --rows 1:15 --columns A:F --offset NEXT_OFFSET --max-bytes 16000
python -m audit.document_tools view documents.json --document DOCUMENT_ID --pages 1:2
python -m audit.document_tools inspect inputs/*.xlsx rules/*.docx --out-dir scratch/snapshots
```

将 DOCUMENT_ID 换成 document-index.json 中的准确 ID。view 直接选择现有索引中的一份快照；单独快照文件也可使用，此时省略 --document。PDF 指定 --table 时查看网格片段，使用 --pages 查看页面上下文。inspect 保存完整快照并返回短清单。段落、表、页、block 和行列均从 1 开始。规则来源要阅读完整条款和必要上下文，不能用默认预览代替完整规则。

view 输出 JSONL。第一行为来源、请求范围、当前 offset、total_records 和 next_offset；后续为 table、merge、alias、cell 或 page 记录。table 给出实际行列选择和 default_cell：只有选择内未另列出的普通格才使用已知 null 默认值。cell 以真实 anchor 坐标保存一次完整值、状态、公式及来源等信息；merge 保留原合并区域，alias 另存无矩形合并表示的位置映射。anchor 在窗口外时仍显示其原始观察作为定位上下文。某位置有独立 metadata 时保留独立 cell，不用别名覆盖它。

默认每页 stdout 至多 16000 UTF-8 字节，可用 --max-bytes 调整，最小为 4096。next_offset 不为 null 时，用相同原件、document、table、rows、columns、pages 及该 offset 继续；offset 是展示记录位置，不是物理行号。next_offset=null 只表示本次所选视图已展示完成，不能证明整个审核已完成。先按规则选择必要区域，确认需要扩大时再改变行列范围；不要为消除分页而打印整个 snapshot。

超长单条记录用 kind=json_fragment 显式分段，record_id 标识原记录，part 从 0 开始，parts 为总段数。收齐后按 part 顺序连接 text，再解析一次 JSON 即得到原始完整记录；不能将一段当作完整字段或页面，也不能按截短文字确认不存在。分段不会改变原快照、公式未知状态或程序求值范围。

## 可选探索

```sh
python -m audit.document_tools bind documents.json --binding binding.json --out scratch/bound.json
python -m audit.document_tools query --table records=scratch/bound.json --queries queries.json --out scratch/results.json
```

bind 的结果只作为读取观察。bind 保留完整物理范围、父子表头路径、原始值、字段状态和 typed citation；排除行与未知行角色属于 use.scopes，不在 Binding 中。

SQL 表包含 row_id、原字段、states_json、evidence_json、scope_state。引用含中文、空格或引号的列时使用 SQL 双引号。SQLite 类型转换、NULL、INNER JOIN 和空集聚合不能替代审核的类型、完整源集合或未知合同。错误、过滤后的零行和未物化的查询都不是完成证据。辅助结果不改变缓存程序，也不成为第二份报告。

## 局部字段观察

```sh
python -m audit.checker_tools documents/target.json --request checks.json --out local_checks.json
```

checks.json 包含同一 physical binding、checks（field_nonempty 或 numeric_at_least），以及可选的同一 BindingScope。此工具调用同一个 evaluator，返回完整违规/未知行号和至多 64 个样例。文本数字不强转为数值，未知公式缓存保持未知。局部条件通过不等于完整客户规则通过。

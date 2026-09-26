# 通用命令

在已安装依赖的 Python 中执行。依赖：XLSX 用 `openpyxl`、XLS 用 `xlrd`、Word 用 `python-docx`、PDF 用 `pymupdf`；SQL 使用标准库 `sqlite3`。设置 `TOOL` 为本 Skill 的 `document_tools.py` 路径。命令没有业务判定，也不自动选择表区。

```bash
python "$TOOL" inspect inputs/*.xlsx inputs/*.xls rules/*.docx --out-dir scratch/snapshots
python "$TOOL" view scratch/snapshots/example.xlsx.json --table Sheet1 --rows 1:15
python "$TOOL" view scratch/snapshots/example.pdf.json --pages 1:2
```

`inspect` 输出完整快照文件和短清单。`view` 只显示指定原始片段，行列为一基索引。PDF 快照有 `pages[].text` 与 `blocks[].bbox/text`；Word 规则表有 `tables[].cells`，普通段落在 `paragraphs[]`。按规则文件全量读取相关条款，不受 `view` 默认行数限制。

根据本次观察写绑定文件。下面的行列仅演示语法，审核时自行确定：

```json
{
  "table": "Sheet1",
  "header_rows": "3:5",
  "body_rows": "6:90",
  "exclude_rows": {"50": "已核对的重复表头，字段排列与原表头相同"},
  "columns": {"operation": "B", "frequency": "K"},
  "mapping_basis": {"operation": "表头路径和正文编号共同确认", "frequency": "抽样的频率子列"},
  "boundary_basis": "完整表头以下，修订履历以上"
}
```

```bash
python "$TOOL" bind scratch/snapshots/example.xlsx.json --config bindings/cp.json --out scratch/cp.json
python "$TOOL" query --table cp=scratch/cp.json --queries queries.json --out query_results.json
```

`bind` 输出父子表头路径、每条记录的有效值、原始地址/合并锚点、未知值和选择数量。只读快照，不再读源工作簿。`body_rows` 可为多个明确区段，例如 `6:30,35:90`；范围外或未选择的行也需要在绑定依据中说明。

`queries.json` 是 `[{"id":"CP.6.count","sql":"SELECT count(*) AS total, sum(empty(frequency)) AS violations FROM cp"}, ...]`。

SQL 表包含 `row_id`、绑定的业务字段及 `evidence_json`。字段保留原始数值/文本类型；转换数值或比较混合类型时显式处理。自带 `empty(value)`（只判断 NULL 或空白字符串）和 `norm(value)`（折叠空白并忽略大小写，保留 NULL）；仅在规则允许这种规范化时使用。

查询结果保存完整命中行，stdout 只返回数量与错误。`evidence_json` 可用于回溯命中记录。查询仅允许只读 SQL，有计算预算；语法错误和预算耗尽返回错误，不能视为零违规。工具不验证任意 SQL 是否忠实表示规则，也不自动给 PASS。

批量运行所有可计算规则。结果中的 `unknown_cells` 必须在判定前检查；空缓存公式不能按真实空值直接归因给供应商。需要看图或处理复杂 PDF 网格时，用已有通用库针对相关区域补充观察，保留来源即可。

## 局部字段检查

`checker_tools.py` 在已冻结的 Excel/Word 表快照上重放最终校验所用的表格谓词。先确认目标文档身份、来源 hash、表头、正文边界、排除行和物理列，再写请求。下面仅示范格式，行列和值必须来自当前文件和有效规则：

```json
{
  "binding": {
    "id": "current-table", "document_id": "target-a", "source_sha256": "<快照中的 SHA-256>",
    "table": "Sheet1", "header_rows": [3, 4], "first_row": 5, "last_row": 90,
    "columns": {"frequency": "K", "measurement": "L"}, "excluded_rows": [],
    "mapping_basis": "本次表头路径与正文记录共同确认", "boundary_basis": "本次确认的完整正文区域"
  },
  "checks": [
    {"id": "frequency-present", "kind": "field_nonempty", "field": "frequency"},
    {"id": "measurement-minimum", "kind": "numeric_at_least", "field": "measurement", "minimum": 0}
  ]
}
```

```bash
python /opt/ppap/semantic/checker_tools.py documents/target-a.json --request checks.json --out local_checks.json
```

完整结果写入 `local_checks.json`，包含来源 hash、原 binding、每项 `local_condition_status`、总数/已检/未知数、完整违规/未知行号，以及至多 64 个违规和未知样例的 `document_id` + typed cell locator；stdout 只给短计数。按完整行号查看后部差异，不能把样例截断当成完整覆盖。空范围为 `UNABLE`；未知公式缓存保留未知；`numeric_at_least` 只接受 Excel 原生数值，文本数字不被强转，Word 数值检查不支持。

这是局部条件观察。`field_nonempty` 可检查频率字段是否有值，但不检查频率值是否满足客户指定频次。只有确认规则确实要求该条件、该字段映射和完整适用范围后，才把结果用于相应条款的推理；最终 query proof 仍由冻结计划与可信校验代码重放。复杂规则的其他分支继续逐项处理，局部 `PASS` 不是整条规则的 `PASS`。

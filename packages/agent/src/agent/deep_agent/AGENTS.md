# PPAP 深度复核

客户审核时，已验证的检查程序已经对目标文件的每条生效条款给出结论、证据和诊断。你在隔离的工作目录中逐条复核这些结论：对照条款原文和原件，认可，或用能在原件定位的证据订正。你不修改、不调用、不重写检查程序。本次规则、文件和程序结论都在工作目录中；本文件是每次都适用的固定要求。

用户消息写明“本次任务：检查程序开发”时，本文件的复核要求不适用，改按技能 `ppap-semantic-audit` 的 `references/checker-development.md` 执行。

## 输入

- `files.json`：同批全部文件（本次目标、同批其他待审文件、参考文件），含 `file_version_id`、原文件名、文档类型和工作目录内的只读路径。
- `rules.json`：本次冻结的条款原文与已确认附录。附录是规则的一部分。
- `program-results.json`：程序对每条的结论（PASS / NG / NA / UNABLE / ERR）、说明、证据位置与摘录、诊断、检查范围。
- `resources-hint.py`（若有）：该类型检查程序的表名、表头锚点、列别名、PDF 配置。只是“在哪找”的线索，不是本文件的版式。

原件只读。不要读取或推测测试预期、评分或历史结论。

## 怎么复核

1. 读条款原文和附录，明确要满足什么：检查范围、全部满足还是至少一个、跨文件比较的方向与基数。
2. 读程序结论、说明和证据。看程序引用的位置是否就是条款要求的内容。
3. 到原件核对。常见的程序错误：表头别名或位置不同、合并单元格、续页或另一张表、读错列、把未读到当成不存在、ERR/UNABLE 其实能读到、PASS 只检查了一部分行。读取方法见技能参考 `references/review-reading.md`。
4. 跨文件条款：按零件号、版本在 `files.json` 中找同批关联文件，自己读取比对；比较的含义见 `references/cross-file.md`，表格结构见 `references/table-reading.md`。
5. 图片、签章、手写签名等视觉事实：用读取工具直接查看图片文件；PDF 页需要看图时先渲染成 PNG。扫描 PDF（页没有文字层）保持 UNABLE，不要靠看图订正。

时间有限：先处理可疑的条款（ERR、UNABLE、证据为空、诊断有警告、说明与原件不符），再核对其余条款。

## 判定

- 认可（`confirmed`）：程序结论与原件相符，或你没有找到足以推翻它的证据。拿不准时认可。
- 订正（`corrected`）：你在原件中找到证据，证明程序结论错误。NG→PASS、PASS→NG、UNABLE/ERR/NA→有证据的结论、PASS/NG→UNABLE（条款要求的内容确实无法从原件确定）都允许。
- 未知不能成为 PASS：PASS 要证明条款要求的每一部分都满足（全部行、全部页、全部关联文件）；只看到一部分不能 PASS。
- NG 要有至少一处明确违反的证据。读不到、不适用、技术失败分别是 UNABLE、NA，不要写成 NG。
- 说明中写有“已按用户决定拒绝自动判定”的条款，以及含义尚待用户确认（`clarifications`）的条款，不要订正为 PASS。

## 证据（宿主逐条核对）

订正至少给一项证据，最多 12 项。每项包含 `file_version_id`、`kind`（`structure` 或 `visual`）、`locator` 和 `excerpt`：

- Excel 单元格：`{"kind":"xlsx_cell","sheet":"工作表名","cell":"B7"}`；区域：`{"kind":"xlsx_range","sheet":"工作表名","start":"A5","end":"F5"}`。
- PDF 页：`{"kind":"pdf_page","page":3}`，摘录取自该页文字层。
- Word：`{"kind":"docx_paragraph","paragraph":12}` 或 `{"kind":"docx_cell","table":1,"row":3,"column":2}`，均从 1 开始。
- 图片文件：`kind` 为 `visual`，`locator` 为 `{"kind":"pdf_page","page":1}`，摘录写你看到的内容。有文字层的 PDF 页上的签章等视觉事实：`kind` 为 `visual`，`locator` 为该页。

`structure` 证据的摘录必须逐字取自原件该位置（单元格原值、页内原文），不要改写、拼接、翻译或补全。宿主用同样的读取方法比对（去掉空白后，摘录必须是该位置原文的一部分）；任何一项核对不过，这条订正作废，保留程序结论。

## 输出

结果写入 `execution.json`：

```json
{
  "file_version_id": "目标文件的 file_version_id",
  "reviews": [
    {"clause_id": "条款ID", "decision": "confirmed", "reason": "简短依据"},
    {"clause_id": "条款ID", "decision": "corrected", "status": "NG", "reason": "原件位置与内容，以及程序为什么错", "evidence": [{"file_version_id": "…", "kind": "structure", "locator": {"kind": "xlsx_cell", "sheet": "PFMEA", "cell": "H12"}, "excerpt": "原值"}]}
  ]
}
```

`reviews` 恰好覆盖 `program-results.json` 的全部 `clause_id`，每条一项。`reason` 用中文写给审核员，说明依据。

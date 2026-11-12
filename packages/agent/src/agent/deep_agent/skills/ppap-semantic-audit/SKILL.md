---
name: ppap-semantic-audit
description: PPAP 深度复核时读取 Excel、PDF、Word、图片和关联文件的参考，以及平台离线开发检查程序的要求。遇到多层表头、合并单元格、PDF、跨文件比对、需要原件命令或检查程序开发任务时读取。
---

# PPAP 语义审核参考

深度复核的固定要求在 `AGENTS.md`，这里只放按情形查阅的参考：

- [复核时读取原件](references/review-reading.md)：Excel、PDF、Word、图片的读取方法，与宿主核对证据的方式一致。
- [通用表格读取](references/table-reading.md)：合并与空白、多层表头、行选择、关联与标准化。
- [跨文件比较](references/cross-file.md)：条款涉及关联文件（例如 PFD 与 CP、特殊特性清单与 PFMEA）时先读。
- [原件命令](references/commands.md)：需要分页查看原件指定区域时使用。

只用于平台离线开发检查程序（用户消息写明“本次任务：检查程序开发”）：

- [检查程序开发](references/checker-development.md)：开发任务的固定要求与检查程序合同。
- [PDF 资源适配](references/pdf-reading.md)：开发含 PDF 的检查程序时先读。
- [写法反例](references/checker-examples.md)：提交前对照，避免不能交付的写法。

---
name: ppap-audit
description: 用 PPAP 审核 PPAP 文件：查询任务和进度、导入文件发起审核、在用户确认后受理、读取结论与报告。用户提到 PPAP 审核、提交件检查、审核进度或审核报告时使用。
---

# PPAP 审核

PPAP 通过 MCP 连接器 `ppap` 提供工具。你代表当前对话的发起人操作，只能看到他在 PPAP 有权访问的任务和文件。

## 基本规则

- 先调用 `get_identity`，确认当前用户和企业。拿不到身份时，请用户在连接器里重新连接 PPAP。
- 每个写操作都要先生成一个新的 UUID 作为 `request_id`，并记住它。没收到回复时，用 `get_operation` 或对应的读取工具（`get_task`、`get_upload`、`get_recognition`）读回原请求；需要继续时，仍用同一个 `request_id` 和同样的参数，不要换新 ID 重试。
- 工具返回 `confirmation_required` 时，把返回的确认链接原样交给用户，请他在浏览器里核对并批准；批准后再用原参数重放同一调用。不要替用户确认，也不要说已经开始审核。
- 结论只按工具返回的原值转述：`PASS` 是“符合”，`NG` 是“不符合”，`UNKNOWN` 是“尚有未确定项”。未知、空结果或技术错误一律不能说成“通过”。
- 不要展示内部 ID、令牌或下载链接中的查询参数。文件名、结论、问题说明可以展示。

## 常见任务

**查询进度或结论**：用 `list_tasks` 找任务，再用 `get_task` 读取状态、逐文件结论和问题数。需要条款细节时用 `get_round`。

**发起审核**：
1. 导入文件：飞书云文档用 `import_feishu_file`，公网链接用 `import_url`。
2. 对每个文件调用 `validate_file`，并用 `get_validation` 读到成功结果。
3. 调用 `prepare_task` 做识别，再用 `get_recognition` 读出识别结果；把文件、识别出的文档类型和规则、不审核的原因、预计点数原样告诉用户。
4. 用户明确说开始后，调用 `confirm_task`，并带上读到的 `expected_revision` 和 `expected_confirmation_basis_digest`。之后按“基本规则”完成浏览器确认。
5. 用 `get_task` 跟进，建议间隔从 5 秒逐步放宽到 30 秒，不要高频轮询。

**报告**：单份报告用 `download_report`；多份报告打包用 `create_report_package`，并用 `get_report_package` 读取结果。

## 群聊里

- 只按发出请求的人的身份操作。没有连接 PPAP 或不在该企业的成员，请他自行连接，或由有权限的同事查询。
- 不在群里贴报告原件；需要时给出 PPAP 中的查看方式，由对方按自己的权限打开。

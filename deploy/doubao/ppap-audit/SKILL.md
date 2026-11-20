---
name: ppap-audit
description: 用 PPAP 审核 PPAP 文件：查询任务和进度、导入文件发起审核、在用户确认后开始审核、读取结论与报告。用户提到 PPAP 审核、提交件检查、审核进度或审核报告时使用。
---

# PPAP 审核

PPAP 通过 MCP 连接器 `ppap` 提供工具。你代表当前对话的发起人操作，只能看到他在 PPAP 有权访问的任务和文件。

## 基本规则

- 先调用 `get_account`，确认当前用户和企业。拿不到时，请用户在连接器里重新连接 PPAP。
- 每个写操作先生成一个新的 UUID 作为 `request_id` 并记住它。没收到回复时，用同一个工具、同一个 `request_id` 再调用一次读回结果；不要换新 ID 重试。
- 工具返回 `confirmation_required` 时，把确认链接原样交给用户，请他在浏览器里核对并确认；他确认后，用同一个 `request_id` 再调用一次。不要替用户确认，也不要在确认前说已经开始。
- 结论只按原值转述：`PASS` 是“符合”，`NG` 是“不符合”，`UNKNOWN` 是“尚有未确定项”。未知、空结果或技术错误一律不能说成“通过”。
- 不展示内部 ID、令牌或下载链接。文件名、结论、问题说明可以展示。

## 一次审核

1. **拿到文件**：用 `find_feishu_files` 找到飞书文件，再用 `import_feishu_file` 导入。导入时会同时检查文件能否读取。
2. **开始审核**：用 `start_audit` 提交这些文件（每个文件给出导入结果里的文件版本 `file.id` 和校验 `validation.id`，`selection` 为 `selected`）。需要写回某个飞书文档时，在这一步给出 `writeback_target`。
3. **核对**：用 `get_audit` 读出识别出的文件类型和规则、预检结论、预计耗时与点数，原样告诉用户。用户要深度模式且可用时，调用 `set_deep_mode`。
4. **确认**：用户明确说开始后，调用 `confirm_audit`，按“基本规则”完成浏览器确认。想在完成时收到飞书消息，带上 `notify_when_done=true`。
5. **跟进**：用 `get_audit` 查看进度，间隔从 5 秒逐步放宽到 30 秒，不要高频轮询。

## 看结果与报告

- **进度或结论**：`list_audits` 找任务，`get_audit` 看逐文件结论和报告状态；条款细节和证据用 `get_result`。
- **深度审核提问**：`get_result` 里有待回答的问题时，把问题原样给用户，按他的回答调用 `answer_question`。
- **人工复核**：用户对某条结论给出判断时，用 `review` 提交，由他在浏览器确认。
- **报告**：单份报告用 `download`；多份打包用 `package_reports`，用同一个 `request_id` 再调用可看进度。发给同事用 `find_recipients` 和 `share_reports`，写进飞书用 `send_to_feishu`。
- **重审**：用 `start_audit` 并给出要重审的 `reaudit_round_id`，其余同上。

## 群聊里

- 只按发出请求的人的身份操作。没有连接 PPAP 或不在该企业的成员，请他自行连接，或由有权限的同事查询。
- 不在群里贴报告原件；需要时给出 PPAP 中的查看方式，由对方按自己的权限打开。

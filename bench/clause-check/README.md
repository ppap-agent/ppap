# clause-check：条款语义审查基准

用一组真实审核条款比较 clause-check 修改前后的**有效发现、误报、漏报和成本**。本基准衡量生成的客户问题是否忠实于原文，不把 JSON 结构校验通过当成语义正确。

## 用例与判分

cases.json 记录各条款、需要对照的真实文件、参照发现（需人工复核的已知问题）和 out_of_scope（不应报告的解释）。参照发现不是不可质疑的真值：要依据最新原文和明确的通用判定政策更新；真正需要避免的是把已经排除的错误解释重新带入问卷。

模型运行后应生成 <条款序号>.review.json，使用 Skill 的 check.py 生成 <条款序号>.report.md 和 <条款序号>.report.json。基准从结构化审查原件**重新生成报告**，评分使用重生成的报告，而不直接相信会话提交的 Markdown。

主要指标：

| 指标 | 含义 |
|---|---|
| 结构校验 | 引文、问题、例子和候选读法满足脚本的输入约定，不代表语义已证明 |
| 报告与脚本一致 | 会话报告是否与结构化审查文件重生成的报告一致 |
| 找到 / 部分 / 漏掉 | 人工参照问题是否被覆盖 |
| 新发现 | 参照之外的发现，需要产品负责人进一步判断 |
| 越界 / 说错 / 重复 | 无根据的扩展、误报、重复问题，应趋近于零 |
| 技术词 / 待补 | 客户报告不应泄漏技术模型名或未完成内容 |
| 被沙箱拒绝 / 用时 / tokens | 执行成本与环境边界 |

特别关注三个误报族：把「A 才通过」当成「A 即通过」；把局部条件通过或例外当整条通过；仅因没有充分条件就问「全部满足还不能通过吗」而没有核对已确认的业务判定政策。是否合理，必须回到条款全文判断。

## 本地校验（仅 Python 标准库）

    python3 -m unittest discover -s .claude/skills/clause-check/tests -v
    python3 .claude/skills/clause-check/scripts/check.py .claude/skills/clause-check/examples/X01/X01-1.review.json

## 运行 Agent 基准

需要在 macOS 配置好 pi、已登录的 claude CLI 和 sandbox-exec。每个会话在独立受限目录中仅能读取本条规则与可选的真实文件，不读仓库中的历史报告、真值表或其他会话状态。

    uv run --no-sync python bench/clause-check/bench.py list
    uv run --no-sync python bench/clause-check/bench.py run --name trial --case U06:3
    uv run --no-sync python bench/clause-check/bench.py run --name trial --jobs 3

基准会复制只读的 Skill 副本、启动隔离会话，然后使用该 Skill 的轻量 check.py 再生报告，并由独立的 Claude Code 评判会话对照 cases.json。无界面运行日志、审查 JSON、再生报告和汇总存放在 bench/clause-check/runs/<名称>/ 下（不入库）。

可重评分：

    uv run --no-sync python bench/clause-check/bench.py score bench/clause-check/runs/<名称> --rejudge

建议同一版本多次重复运行，记录模型随机性造成的波动，再对比不同提交。不要把「发现数量上升」当成质量提高，也不要把退出码 0 当成客户问题有效性的证据。

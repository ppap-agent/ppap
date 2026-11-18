# clause-check：条款检查技能的长期试验

用真实条款衡量 [clause-check](../../.claude/skills/clause-check/SKILL.md) 技能的实际效果：让技能从头完整分析一条条款，看报告找到了多少已知问题、漏了什么、有没有越界、白话写得怎么样，以及每条花多少时间和费用。技能每次改动前后在同一组条款上各跑一遍，比较汇总。

它衡量的是技能产出的报告，不证明条款本身对错，也不替产品负责人裁决任何问题。

## 用例

[`cases.json`](cases.json)：

- `cases`：每条条款的规则文件、需要对照的真实文件（可为空），以及**参照发现**，即已知原文没说清的地方。参照来自前期复核和产品负责人的判断，只写条款字面上的问题。
- `out_of_scope`：报告不应出现的分析，例如引入原文没有的判断栏、按行业惯例补要求、替规则选定理解。

参照不是标准答案：评判列出的“新发现”由产品负责人判断，有用的补进对应条款的 `reference`，越界的补进 `out_of_scope`，用例随实践不断积累。

## 运行

需要已登录的 `claude` 命令行，以及技能本身的运行条件（`JAVA_HOME` 指向 Java 11 及以上）。每条条款启动一个无界面的 Claude Code 会话，按技能从读条款开始完整走一遍。会话不能读 `bench/` 下的内容，但这只是权限设置加提示，不是严格隔离。

```bash
uv run --no-sync python bench/clause-check/bench.py list
```

```bash
uv run --no-sync python bench/clause-check/bench.py run --name trial --case U06:3
```

```bash
uv run --no-sync python bench/clause-check/bench.py run --jobs 3
```

技能的工作文件在 `.local/clause-check-bench/<名称>/<条款>/`；报告、形式化文件、会话摘要（`session.json`）和完整会话记录（`transcript.jsonl`）复制到 `bench/clause-check/runs/<名称>/<条款>/`（不入库），回看技能每一步怎么走的就看会话记录。

会话用的是本机已登录的 Claude Code 配置，所以也会带上本机的用户记忆和项目会话列表；要完全隔离，需要单独的配置目录加单独的登录凭据，目前没有这样做。

运行结束后自动评分；重新评分：

```bash
uv run --no-sync python bench/clause-check/bench.py score bench/clause-check/runs/<名称> --rejudge
```

## 指标

`runs/<名称>/summary.md` 每条条款一行：

| 指标 | 含义 |
|---|---|
| 找到 / 部分 / 漏掉 | 参照发现在报告里被指出、只说到一部分、没有指出（由评判会话逐条比对） |
| 新发现 | 报告里不对应任何参照的问题，留给产品负责人判断是否有用 |
| 越界 | 报告里属于 `out_of_scope` 的分析，应为 0 |
| 技术词 | 报告里的技术词汇和英文标识符（条款原文和文件名里的词除外），应为 0 |
| 待补 | 报告里“待补白话说明”的处数，应为 0 |
| 用时 / 费用 | 技能会话的耗时和费用，不含评判 |

表下列出每条条款没找到的参照和全部新发现、越界项。

## 比较两种做法

每种做法是一个提交。分别在各自提交上运行，比较两份 `summary.md`。模型有随机性，比较前在同一提交上重复几次，看波动范围。结论写进对应的 Issue 或 PR；用例和参照的变化随技能改动一起提交到 `cases.json`。

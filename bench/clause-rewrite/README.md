# clause-rewrite：条款改写基准

用同一份输入，让两套做法各改写一遍规则库条款，再比较结果：

- **grok 工作流**：[`.agents/skills/clause-rewrite/workflow/clause-rewrite.rhai`](../../.agents/skills/clause-rewrite/workflow/clause-rewrite.rhai)，复制到工作区的 `.grok/workflows/` 后在 grok 会话里按脚本路径运行（第一次要在界面里信任这个目录），运行目录里的报告和完整结果再原样复制到 `out/`；
- **Claude + 技能**：无界面的 Claude 会话读 [clause-rewrite 技能](../../.agents/skills/clause-rewrite/SKILL.md) 照做，独立核对交给另起的子 agent。

两边都在 `/private/tmp/clause-rewrite-bench/<名称>/` 下各自的目录里运行，只看得到输入和技能，看不到本仓、旧修订、草稿、审查记录和检查程序。Claude 一侧另有 macOS 沙箱，挡住本仓、其他项目和会话记录。人工纠正后的修订 3 草稿只作参照，不是标准答案。

## 输入

`inputs/uaes-v1.0-m0/`（入库，固定不变）：

- `answers.json`：问卷导出快照，28 张卡片及全部答复（导出时间见文件；权威数据在产品库）；
- `rulings.md`：产品负责人裁定原话，每节一个依据编号 `ruling:…`。

另外两份输入不复制：

- 底稿条款原文直接读 `assets/uaes/rulebook/v1.0/m0/rules/`；
- 已删除的通用判定说明在运行时从 git 历史取出（见 `cases.json` 的 `notes_source`），按节拆成依据 `ref:…`。

## 检查

`cases.json`：

- `checks`：脚本按字面判，例如来历字眼不得残留、带规则的理由句不得删；
- `judge`：人工改写时实际犯过的错，交给看不到来源的评判会话对照依据判。Claude 和 grok 各评一遍，两边交叉看；有争议的交产品负责人。

它们是已知会犯的错，不是全部标准。

## 运行

```bash
python3 bench/clause-rewrite/bench.py prepare --name r1
python3 bench/clause-rewrite/bench.py grok-prompt --name r1   # 在 /private/tmp/clause-rewrite-bench/r1/grok 里开 grok，发这段话
python3 bench/clause-rewrite/bench.py run-claude --name r1
python3 bench/clause-rewrite/bench.py collect --name r1
python3 bench/clause-rewrite/bench.py check --name r1
python3 bench/clause-rewrite/bench.py judge --name r1
python3 bench/clause-rewrite/bench.py report --name r1
```

产出在 `runs/<名称>/`（不入库）：两边的 `result.json` 与报告、参照草稿、`compare/` 下的检查、评判和 `summary.md`。

脚本只准备输入、启动会话、做字面检查和汇总，不写、不改、不判条文的意思。

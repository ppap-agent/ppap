# pure-audit：不用检查程序的审核

让 [auditor](../../packages/auditor/README.md) 只凭条款和原件审一个规则库修订的基准批次，看它与参考判定差在哪里、证据站不站得住、重复运行结论稳不稳，以及花多少时间和费用。对照的另一边是 [deep-audit](../deep-audit/README.md)（检查程序先判、模型再订正）。

## 给它什么、不给什么

- 给：该修订的签署条文（`assets/uaes/rulebook/v1.0/<修订>/条文`）和批次的原件。
- 不给：检查程序及其结论、表头线索、参考判定、期望角色。
- 待审文件的文档类型取自基准，用来选条款；识别不是这里要测的。
- 文件在工作目录里改成中性名字（`01.xls`、`02.pdf`）：基准的文件名和目录名写着植入的缺陷，而文件名本来就不是证据。

参考判定是模型判定，不是人工真值：汇总只列差异，不算准确率。

## 运行

需要 Docker，主检出里的 `.pi/agent`（Pi 的模型配置）和 `.env`（视觉模型，用于转写）。模型与线上深度复核相同；视觉模型的推理强度取 `deploy/tke/current.yaml`。

```bash
uv run --no-sync python bench/pure-audit/eval.py run --revision m4 --batch "F01R10DP4Z_名称待确认" --name trial
```

```bash
uv run --no-sync python bench/pure-audit/eval.py run --revision m4 --batch "F01R20V040_磁性罩" --thinking high --repeat 3 --name v040-high
```

| 选项 | 含义 |
|---|---|
| `--batch` / `--all` | 批次名，见基准的 `batches.jsonl`，可重复；`--all` 是基准的全部批次。几个批次在同一个容器里共用一组审核员 |
| `--resume` | 接着跑同名运行：已有结论的条款和读完的文件不再做 |
| `--thinking` / `--hard-thinking` | 阅卷、审核、质证的推理强度 / 专审、裁决的推理强度（默认 medium / high） |
| `--seconds` | 审一条条款的时限（默认 600）；专审和裁决同此，质证取三分之二 |
| `--read-seconds` | 阅卷一份文件的时限（默认 360） |
| `--concurrency` | 同时工作的审核员数，所有批次合计（默认 12） |
| `--repeat` | 同样的输入重复几次，各成一次运行 |
| `--no-read` `--no-focus` `--no-challenge` `--no-transcribe` | 关掉阅卷、专审、质证（及裁决）、准备阶段的预先转写，用来看每一步带来了什么 |
| `--experience 目录` | 审前读入、阅卷后追加同类文件的读法经验 |
| `--source` | 把工作区的 `packages/auditor/src` 挂进容器，改代码不用重建镜像 |
| `--build` | 先重建镜像 |

结果在 `runs/<名称>/`（不入库）：`results.jsonl`（每条结论及其要点、证据、第一遍结论、是否经质证改判）、`summary.md`、`summary.json`，以及每个批次的工作目录（Pi 事件、看过的范围、被退回的提交）。

## 看结果

```bash
uv run --no-sync python bench/pure-audit/eval.py report bench/pure-audit/runs/trial
```

```bash
uv run --no-sync python bench/pure-audit/eval.py report bench/pure-audit/runs/v040-high-r1 bench/pure-audit/runs/v040-high-r2 bench/pure-audit/runs/v040-high-r3
```

```bash
uv run --no-sync python bench/pure-audit/eval.py report bench/pure-audit/runs/trial --deep bench/deep-audit/runs/<名称>
```

给多个运行时，另列出结论不一致的行，以及其中通过与不通过互换的行。`--deep` 与一次深度复核运行逐行对照。

## 看会话

```bash
uv run --no-sync python bench/pure-audit/sessions.py bench/pure-audit/runs/trial
```

把一次运行的每个审核员会话读一遍，写出 `sessions.md`：各步骤的耗时、请求数、到首次提交用了多久、没命中缓存的请求、打开了多少图片；工具给审核员的坏消息按种类计数；审核员绕开 `look` 自己跑的命令、走出自己工作目录的次数；最费时的会话。它只计数和列举，用来找工具和任务说明该改的地方。

| 汇总项 | 含义 |
|---|---|
| 与参考一致（第一遍审核后） | 最终结论与第一遍结论各自等于参考判定的行数，差值是专审、质证和裁决带来的变化 |
| 未在时限内给出结论 | 所有步骤之后仍没有结论的条款，记为待确认 |
| 质证后改变结论 / 交裁决 | 第二名审核员改判的条款，以及交第三名审核员定夺的条款 |
| 提交被退回 | 引文对不上、声明查过的范围没看全、或结论与要点不符而被拒的提交次数 |
| 证据 | 逐字核对通过的引文数；所见类引文数，以及其中与视觉模型独立转写一致的数量 |
| 耗时 | 全程和各阶段的墙上时间；一条条款从开审到定论的中位和最长秒数；各步骤的会话秒数及其中花在工具上的秒数 |
| 用量与费用 | Pi 各步骤的 token，按 [prices.json](../deep-audit/prices.json) 的公开价计算，分步骤、分文件列出；不含转写 |

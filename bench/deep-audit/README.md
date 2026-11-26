# deep-audit：深度审核评估

衡量深度模式在客户实际用的检查程序上做了什么：检查程序给出结论后，深度复核把多少条改得与参考一致、改得不一致，有没有新给出与参考不符的 PASS，程序待确认的条款复核后定下了多少，以及每份文件的检查、复核耗时和按公开价计算的模型费用。可调的只有复核推理强度，用于强度选型。

## 跑什么、对照什么

- **检查程序**：永远是已安装的 UAES 叶子（`packages/audit/src/audit/checkers/uaes`），即线上在跑的那套。不换候选、不用旧版本。
- **对照的修订**：每次运行必须用 `--revision` 指定，例如 `m4`。用该修订的基准 `assets/uaes/rulebook/v1.0/candidates/<修订>/benchmark`（批次、原件、模型参考判定）和签署条文 `assets/uaes/rulebook/v1.0/<修订>/条文`。修订没有基准或条文就报错，不退回别的修订。
- **条文核对**：运行前逐条比较已安装条文与该修订条文，写法不同的条款写进 `meta.json`，在汇总开头列出，相关行照常对照。例如线上是修订 5 的条文（修订 4 加上已答复的卡片），对照 `m4` 时会列出改了写法的 10 条。
- **参考判定**是模型判定，不是人工真值：只看差异和原因，不算准确率。

## 走的是哪条路径

每个基准批次只按原件和提交角色重新提交，识别与预检走产品路径（同 `corpus_contracts.candidate_run --benchmark`）；审核这一步与线上 Controller 相同：在从 `HEAD` 构建的深度镜像里，以 root 启动本地模型网关和 `ContainerRuntime`，guest 降权为 uid 10001、在 `prlimit` 下运行，由 `CoreRuntime.execute_snapshot` 在一个 guest 中执行整轮、同组共享上下文：逐份运行检查程序，再由 Pi 按 `deep_agent/AGENTS.md` 几份并行复核（并行数见 `meta.json` 的 `review_concurrency`），宿主校验并合并订正。每份的时限取线上值（`PPAP_DEEP_REVIEW_SECONDS` 等，见 [deploy/deep](../../deploy/deep/README.md)）。某份复核未完成时，这份保留程序结论，记为未复核。

- 模型：平台模型（`.env` 中的 `PPAP_MODEL_TEXT_*` / `VISION_*` / `CODING_*`），按未配置企业 Key 的租户冻结身份；推理强度、复核时限、沙箱配置取线上值（`deploy/tke/current.yaml`）。`--effort low|medium|high|xhigh` 只改复核强度；实际的复核模型、强度和时限写进 `meta.json`。
- 识别与预检的模型请求先用 `assets/corpus/replay` 中请求完全相同的录音回答，没有才现调，且不保存。
- 用量与费用：审核的每次模型请求都经过本进程托管的网关，逐次记下用途、模型、实际强度和服务商返回的用量（输入、其中缓存命中、输出、其中推理），按发出时正在进行的任务归到对应文件的检查或复核。费用按 [prices.json](prices.json) 的公开价计算（美元），价格表由人维护，写明来源与读取日期。

它不证明产品的 guest 网络隔离、Worker 调度或积分计费，也不证明规则解释正确。

## 运行

需要 Docker、本仓 `.env` 中的平台模型配置，以及干净的工作区（镜像与基准必须是同一提交）。

```bash
bench/deep-audit/run.sh --revision m4 --jobs 4
```

```bash
bench/deep-audit/run.sh --revision m4 --name effort-high --jobs 4 --effort high
```

```bash
bench/deep-audit/run.sh --revision m4 --name trial --jobs 1 -- --batch F01R10DP4Z_名称待确认
```

`--` 之后的参数传给 `deep_eval.py run`：`--batch`（可重复，批次名见基准的 `batches.jsonl`）。只看条文核对：

```bash
uv run --no-sync python bench/deep-audit/deep_eval.py clauses --revision m4
```

结果写在 `bench/deep-audit/runs/<名称>/`（不入库）：`results.jsonl` 与 `candidate_run --benchmark --json` 同一格式（识别、检查用时、每条结论），结论行另带程序原结论、复核决定与理由、文件复核状态，每批一行 `deep` 记每份文件的复核状态、检查与复核耗时和逐次请求；也可以直接交给 `corpus_contracts.benchmark_diff` 看复核后的差异。`summary.json` 与 `summary.md` 为汇总，并逐条列出新增与参考不符的 PASS、改成不一致和订正被拒。重算汇总：

```bash
uv run --no-sync python bench/deep-audit/deep_eval.py report bench/deep-audit/runs/<名称>
```

## 指标

| 指标 | 含义 |
|---|---|
| 程序与参考一致 / 复核后与参考一致 | 在完成的批次上，按基准固定的（批次, 文件, 条款）行，结论等于参考判定的行数；没有输出的行照样计入 |
| 改成一致 / 改成不一致 / 改了仍不一致 | 复核改变了结论：由不一致变一致 / 由一致变不一致 / 改了仍不一致 |
| 新增与参考不符的 PASS | 复核把结论改成 PASS，而参考不是 PASS。深度模式可以给 PASS，这一项要逐条看 |
| 与参考的分歧 | 程序与复核后各自按 `benchmark_diff` 的类别分布（程序通过参考不通过、程序待确认参考有结论等） |
| 未复核条款 | 所在文件复核失败或超时，保留程序结论 |
| 整轮失败 | 整轮执行失败（如请求数达到上限）的批次没有结论，不计入对照，单列批次、行数、耗时、请求数与费用；已花的费用计入合计 |
| 订正被拒 | 复核给出的订正没有通过宿主的证据核对，保留程序结论 |
| 待确认被定下 | 程序结论为 UNABLE 的条款，复核后给出结论的条数，及其中与参考一致的条数 |
| 费用 | 公开价下的美元费用：合计、按检查 / 复核、按模型与强度，以及每份文件检查和复核费用的中位 / P90 / 最大；缓存命中占输入的比例；单批最多请求数 |
| 每份检查 / 复核耗时 | guest 每开始一项任务就报告一次进度；检查逐份进行，持续到下一项开始；复核几份并行，用 guest 自己量的每份用时；与线上每份检查、复核的时限对照 |
| 复核的每次模型请求 / 失败的模型请求 | 网关从发出到结束的用时（含等网关空位）与服务商返回失败的请求按状态分布，用来看并行复核是否拥塞；几份同时在复核时发出的请求只计入合计，不归到单份文件 |

## 比较两种做法

每种做法是一个提交。分别在各自提交上对同一修订运行，再比较两份 `summary.md`。模型调用有随机性，比较前在同一提交上重复几次，看波动范围。结论写进对应的 Issue 或 PR，不在仓库里另建实验台账。

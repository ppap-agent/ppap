# deep-audit：深度审核评估

用 UAES 人工 Oracle 衡量深度模式，为复核模型和推理强度选型：检查程序给出结论后，深度复核把多少条改对、改错，有没有把不该通过的改成 PASS，程序无法判断的条款复核后定下了多少，以及每份文件的检查、复核耗时和按公开价计算的模型费用。

## 走的是哪条路径

与线上 Controller 相同：在从 `HEAD` 构建的深度镜像里，以 root 启动本地模型网关和 `ContainerRuntime`，guest 降权为 uid 10001、在 `prlimit` 下运行，由 `CoreRuntime.execute_snapshot` 在一个 guest 中执行整轮、同组共享上下文：逐份运行已验证的 Suite 程序（允许模型读取，每份 3 分钟），再逐份由 Pi 按 `deep_agent/AGENTS.md` 复核（每份 6 分钟），宿主校验并合并订正。某份复核未完成时，这份保留程序结论，记为未复核。

- 模型：平台模型（`.env` 中的 `PPAP_MODEL_TEXT_*` / `VISION_*` / `CODING_*`），按未配置企业 Key 的租户冻结身份；推理强度、请求上限、沙箱配置取自 `.env.production`。检查用文本与看图模型，复核（Pi）用 coding 模型，强度为 `PPAP_MODEL_CODING_REASONING_EFFORT`（网关以配置的强度为准）。
- 选型：`--coding text|vision` 让复核改用文本或看图模型的地址、Key 与模型；`--effort low|medium|high|xhigh` 改复核强度。检查用的模型不变，所以比较的只有复核。实际的复核模型与强度写进 `meta.json`。
- 用例：`assets/corpus` 中 UAES 的人工 Oracle（`uaes-cases.csv`、`uaes-oracle.csv`、`uaes-reviewed-rules.json`）。语料合同照常检查条款正文与人工复核语义是否一致，不一致即报错，不评测过期真值。
- 用量与费用：每次模型请求都经过本进程托管的网关，逐次记下用途、模型、实际强度和服务商返回的用量（输入、其中缓存命中、输出、其中推理），按发出时正在进行的任务归到对应文件的检查或复核。费用按 [prices.json](prices.json) 的公开价计算（美元），价格表由人维护，写明来源与读取日期。
- 引用文件按上传流程识别，与线上冻结方式一致；识别请求先用 `assets/corpus/replay` 的录音回答，没有录音才现调模型，且不保存。这样两次运行只在审核本身不同。

它不证明产品的 guest 网络隔离、Worker 调度或积分计费，也不证明规则解释正确；只衡量同一批人工真值上的结论质量、时间与模型费用。

## 运行

需要 Docker、本仓 `.env` 中的平台模型配置，以及干净的工作区（镜像与语料必须是同一提交）。

```bash
bench/deep-audit/run.sh --jobs 3
```

```bash
bench/deep-audit/run.sh --name deepseek-high --jobs 4 --coding vision --effort high
```

```bash
bench/deep-audit/run.sh --name trial --jobs 1 -- --case 0 --case 5
```

`--` 之后的参数传给 `deep_eval.py run`：`--case`（可重复）。先看用例编号：

```bash
uv run --no-sync python bench/deep-audit/deep_eval.py run --out /tmp/unused --list
```

结果写在 `bench/deep-audit/runs/<名称>/`（不入库）：`cases.jsonl` 每例一行（逐条真值、程序结论、复核后结论、复核决定与理由；每份文件的复核状态、检查与复核耗时、复核用量），`summary.json` 与 `summary.md` 为汇总，并逐条列出新增与真值不符的 PASS、改错和订正被拒。重算汇总：

```bash
uv run --no-sync python bench/deep-audit/deep_eval.py report bench/deep-audit/runs/<名称>
```

## 指标

| 指标 | 含义 |
|---|---|
| 程序正确 / 复核后正确 | 结论等于人工真值的条款数 |
| 改对 / 改错 / 错改错 | 复核改变了结论：由错变对 / 由对变错 / 改了仍错 |
| 新增与真值不符的 PASS | 复核把结论改成 PASS，而人工真值不是 PASS。深度模式可以给 PASS，但这一项应为 0 |
| 未复核条款 | 所在文件复核失败或超时，保留程序结论 |
| 订正被拒 | 复核给出的订正没有通过宿主的证据核对，保留程序结论 |
| 无法判断被定下 | 程序结论为 UNABLE 的条款，复核后给出确定结论的条数，及其中与真值一致的条数 |
| 费用 | 公开价下的美元费用：合计、按检查 / 复核、按模型与强度，以及每份文件检查和复核费用的中位 / P90 / 最大；缓存命中占输入的比例；每份与单轮请求数（对照每轮 `PPAP_DEEP_MODEL_MAX_REQUESTS`） |
| 每份检查 / 复核耗时 | guest 每开始一项任务就报告一次进度，一项任务持续到下一项开始；与线上时限（检查 3 分钟、复核 6 分钟）对照 |

## 比较两种做法

每种做法是一个提交（分支）。分别在各自提交上运行，再比较两份 `summary.md`。模型调用有随机性，比较前在同一提交上重复几次，看波动范围。结论写进对应的 Issue 或 PR，不在仓库里另建实验台账。

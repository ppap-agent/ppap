# deep-eval：深度审核评估

用人工 Oracle 衡量深度模式：检查程序给出结论后，深度复核把多少条改对、改错，有没有把不该通过的改成 PASS，以及每例耗时和模型用量。

## 走的是哪条路径

与线上 Controller 相同：在从 `HEAD` 构建的深度镜像里，以 root 启动本地模型网关和 `ContainerRuntime`，guest 降权为 uid 10001、在 `prlimit` 下运行，先执行已验证的 Suite 程序（允许模型读取），再由 Pi 按 `deep_agent/AGENTS.md` 复核，最后由 `CoreRuntime.execute_snapshot` 校验并合并订正。整次深度执行失败时，和线上一样回退为程序结论，对应文件记为未复核。

- 模型：平台模型（`.env` 中的 `PPAP_MODEL_TEXT_*` / `VISION_*` / `CODING_*`），按未配置企业 Key 的租户冻结身份；复核强度、请求上限、沙箱配置取自 `.env.production`。
- 用例：`assets/corpus` 中一家客户的人工 Oracle（`<客户>-cases.csv`、`<客户>-oracle.csv`、`<客户>-reviewed-rules.json`），`--dataset li|nio|uaes` 选择，默认 `uaes`；三家分开评估、分开汇总，不混在一起。语料合同照常检查条款正文与人工复核语义是否一致，不一致即报错，不评测过期真值。
- 引用文件按上传流程识别（会调用模型），与线上冻结方式一致。

它不证明产品的 guest 网络隔离、Worker 调度或计费，也不证明规则解释正确；只衡量同一批人工真值上的结论质量与成本。

## 运行

需要 Docker、本仓 `.env` 中的平台模型配置，以及干净的工作区（镜像与语料必须是同一提交）。

```bash
deep-eval/run.sh --jobs 3
```

```bash
deep-eval/run.sh --name trial --jobs 1 -- --case 0 --case 5
```

`--` 之后的参数传给 `deep_eval.py run`：`--dataset`、`--case`（可重复）。先看用例编号：

```bash
uv run --no-sync python deep-eval/deep_eval.py run --dataset uaes --out /tmp/unused --list
```

结果写在 `deep-eval/runs/<名称>/`（不入库）：`cases.jsonl` 每例一行（逐条真值、程序结论、复核后结论、复核决定与理由、文件复核状态、耗时与用量），`summary.json` 与 `summary.md` 为汇总。重算汇总：

```bash
uv run --no-sync python deep-eval/deep_eval.py report deep-eval/runs/<名称>
```

## 指标

| 指标 | 含义 |
|---|---|
| 程序正确 / 复核后正确 | 结论等于人工真值的条款数 |
| 改对 / 改错 / 错改错 | 复核改变了结论：由错变对 / 由对变错 / 改了仍错 |
| 新增与真值不符的 PASS | 复核把结论改成 PASS，而人工真值不是 PASS。深度模式可以给 PASS，但这一项应为 0 |
| 未复核条款 | 所在文件复核失败、超时或整次深度执行失败，保留程序结论 |
| 订正被拒 | 复核给出的订正没有通过宿主的证据核对，保留程序结论 |

## 比较两种做法

每种做法是一个提交（分支）。分别在各自提交上运行，再比较两份 `summary.md`。模型调用有随机性，比较前在同一提交上重复几次，看波动范围。结论写进对应的 Issue 或 PR，不在仓库里另建实验台账。

# Typed Evidence Hole：共享绑定与本地完整文档实验

研究问题：怎样提高现有测试的准确率，减少重试与 token，同时保持 Deep 的结构简单？

当前结论：**复用一个 grok-build core，让模型解决共享证据绑定，让代码读取原件并计算结果。** Typed Evidence Hole 是尚未解决的有类型字段，不需要独立调度器、依赖图或另一套 agent 平台。

## 当前实现

```text
冻结规则及原件 → 宿主读取完整原件 → grok-build 返回共享 Binding
                                            ↓
                                  宿主取值、计算、保存结果
```

`full_document_experiment.py` 本地启动 `grok --agent grok-build`，固定请求模型 `grok-4.7`、reasoning=`medium`。本次 core 为 `grok 1.0.70 (f50904eaee6c)`。研究流程直接运行，不需要 Docker。

`bound_document.py` 的 Binding 只包含字段位置、记录集合、列映射和未解决项。模型不输出最终状态、计数或计算脚本。宿主从冻结原件重新取值，复用现有 Standard 的纯测量策略判断 R3/R5/R7。多条规则共享同一记录范围，不能因某行非数值而将它从源记录中删除。

这是冻结 Doc01 八条规则的显式实验；原始规则 SHA 必须匹配。它还不能执行任意用户规则，也没有迁入产品 Binding。产品执行链另已删除重复模型报告：模型生成 `AuditTrace`，代码派生原 `AuditReport`，外部结果格式保持不变。

`inspect_original.py` 读取 XLSX 的全部工作表、合并范围和图片关系；`inspect_pdf.py` 直接读取原始 PDF，保留逐页网格、原件 SHA、单元格 bbox 和页面 PNG，不读取 XLSX 同名文件。5 份 PDF 共 25 页已实际读取。页旋转、非零裁剪原点明确拒绝；图片和未重建网格仍可保持未知。

部分说明页的推断网格 bbox 超出页面边界；独立检查确认相关非空文字实际在可见页内。当前保存的是原始网格坐标，不能把它当作已裁剪的显示引用。

原生值的使用按每个有效源单元格判断：已知非空值可用于原生单元格检查，logo 或印章重叠不将该值自动变成未知。图片中的值、未知公式和未解决绑定仍是未知；同组其他已知样本不能掩盖图片样本。此策略不证明视觉无遮挡。单行数值组保留分隔边界，换行单元格保留现有测量策略的内部规整语义。

PDF 续页允许 `header_rows=[]`，表头行号只属于当前页。表头与数据重叠、地址越界、不同记录重复使用同一测量来源仍被拒绝。物理行完整不等于逻辑分组正确；共享绑定也会共享错误。

## 已执行结果：2026-10-28

人工期望来自现有 canonical CSV：`assets/全量测试 - 4.8文件测试反馈（云端） - 4.8（云端）.csv`。`score_corpus.py` 复用 canonical loader、严格比较器和现有报告格式，不使用 baseline exception。

| 范围 | 严格匹配 | 假 PASS | 假 NG | UNABLE |
| --- | ---: | ---: | ---: | ---: |
| 全部 Doc01 XLSX：5 文件 | **40/40** | 0 | 0 | 0 |
| 全部 Doc01 PDF：5 文件 | **38/40** | 0 | 0 | 2 |
| 全部 Doc01：10 文件 | **78/80，97.5%** | 0 | 0 | 2 |

最终派生结果在 `output/native-replay-03/`，严格评分在 `output/doc01-native-final-03.json`。因为仍有两项不匹配，评分命令正确返回非零；不能称为全部通过。

两项未解决：

- 亚大 PDF R8：推断网格将供应商值与责任人期望区合并；模型的责任人 absent 声明与合并源值冲突。宿主保留 UNABLE，没有将供应商名称当成责任人。
- 继峰遮挡 PDF R3：模型保留了源记录范围未解决，涉及未覆盖网格及附带模板的区分。其他明确违规仍可独立 NG。

本次全量执行覆盖 Doc01 的全部 80 条，但不是全 V2/V3。完整 V2 有 164 文件、840 条；V3 有 8 组、27 文件、220 条。其他文档规则、关联文件和扫描件尚未在本入口完成。所有已观察样本都是开发数据，不能当作独立泛化测试集。

### 同三份 XLSX 的成本对照

继峰、亚大、继峰错误原件字节不变，模型配置均为 `grok-4.7` / `medium`。

| 项目 | 旧流程：模型绑定、写脚本及结果 | 当前共享绑定及宿主重放 |
| --- | ---: | ---: |
| 人工期望匹配 | 21/24 | **24/24** |
| 模型请求，含辅助请求 | 54 | **7** |
| 输入 tokens，含缓存 | 2,373,442 | **63,013** |
| 输出 tokens | 112,504 | **28,786** |

旧结果的错误为亚大 R3 假 PASS、R7 假 NG，以及继峰 R3 UNABLE。当前按既有业务策略计算，原候选没有被覆盖。输入包含缓存命中，因此 token 降幅不能直接视为费用降幅；这也不是随机化或重复运行的性能实验。

### 本次全部尝试

| 执行组 | 模型请求 | 输入 tokens | 输出 tokens |
| --- | ---: | ---: | ---: |
| 5 份 XLSX 首次运行 | 11 | 118,999 | 49,941 |
| 5 份 PDF 首次运行，含 2 次结构失败 | 11 | 164,387 | 42,198 |
| 2 份 PDF 各一次修复 | 5 | 181,694 | 21,782 |
| **合计** | **27** | **465,080** | **113,921** |

两次结构失败来自强制非空的续页表头字段。修正合同后，使用原候选和明确校验反馈各修复一次。没有无限修复、未知消费重放或失败后换模型。所有请求用量已结算。

首轮 XLSX 为 35/40；原生值与图片策略修正后，以同一模型绑定离线重放得到 40/40。PDF 单元格内的数值组与换行处理也通过宿主重放验证。`native-replay-01/02/03` 保留各次派生结果；这些重放共新增 **0 次模型请求**。

## 证据与训练记录

每例保存原件副本、完整读取快照、提示、schema、运行器与判定器源码、脱敏请求、可观察动作、图片字节、模型绑定、计算结果、用量和失败。运行原件和规则均核对 SHA。`replay_bindings.py` 重新读取原件并比较冻结的源观察；只有已完成且用量已结算的运行可重放。每份新结果记录原结果、快照及判定器指纹，不覆盖原结果。

Gateway 将真实凭据留在父进程，CLI 只用短期令牌。macOS Seatbelt 先验证原件可读、范围外探针不可读。未保留原始 core 会话、私有推理块或 provider key。本轮 731 个已保存文件的 key 扫描无命中。该本地研究不证明产品部署隔离或完整客户路径。

已有人工业务标签；独立人工字段绑定 gold 仍为 **0**。继峰、亚大、巨一有独立 agent 的原件复核，均明确 `human_gold=false`。继峰若干记录只有序号合并，而检查项目独立；即使八条状态正确，也不能将这些分组当成金标。所有产物保持 `semantic_binding=unverified`、`record_coverage=model_declared|unresolved`、`training_eligible=false`。

现有策略和人工期望也不是物理真值证明。巨一 XLSX 的正下偏差解析、几何公差跳过及可疑测量字符串已在独立复核中记录。例如 `D214=平面度0.05`、`J214=0.0987` 与当前 R3 PASS 存在张力。不能把状态匹配直接训练成正确公差推理；本轮未改人工 CSV 或既有 Standard 策略。

未来标注应分别确认字段含义、完整记录范围、分组、缺失原因及业务结论。按原始来源和模板族分割数据；同一文档的 PDF/XLSX、错误版本、遮挡版本和坐标平移不能跨训练与测试集合。本轮数据只保存在本地，尚未导出或用于训练。

## 运行与验证

先安装 workspace 依赖。每次输出目录必须不存在；以下 `--live` 会真实调用模型。

```bash
uv run --no-sync python packages/ppap_deep/prototypes/typed_evidence_holes/full_document_experiment.py \
  --live --settings-root /path/to/configured-repo --assets-root . \
  --output /private/new-full-run
```

默认运行 5 XLSX + 5 PDF。每例最多 8 次请求、20 分钟、30 万输入和 4 万输出 tokens，无 SDK 自动重试。只有用量已结算的首次结构拒绝可显式使用 `--repair-from PREVIOUS_RUN --case CASE`，且只允许修复一次。

```bash
uv run --no-sync python packages/ppap_deep/prototypes/typed_evidence_holes/replay_bindings.py \
  /path/to/completed-case --output /private/new-replay

uv run --no-sync python packages/ppap_deep/prototypes/typed_evidence_holes/score_corpus.py \
  --corpus v2 --doc-key 1 --output /private/new-score.json \
  /path/to/replay/*/execution.json

uv run --no-sync pytest packages/ppap_deep/prototypes/typed_evidence_holes/test_*.py -q
```

研究测试 59 项通过，覆盖未知值、原生/图片混合、合并引用、数值组、续页、冻结来源、显式修复与评分分母。Ruff、format、排除生成输出后的 mypy 均通过。产品 trace 派生的 146 项 Deep 测试及共享策略的 606 项 engine owner 测试在前一提交通过；这不替代全 V2/V3 的真实模型准确率验证。

## 历史实验

旧原件完整执行及启动失败记录仍在 `output/full-grok47-medium-*`；包括历史失败，共 91 次已确认请求、3,429,143 输入及 176,760 输出 tokens，与本次 27 次分开记账。旧三份完成产物用于上表的同源对照。

最初局部对比使用 `grok-4.6` / `low`，共 24 次请求；`hole_experiment.py`、`probe_column_type.py`、`evaluate_rows.py` 和 `demo.html` 保留可运行入口。历史局部参考来自 agent 检查，不是独立人工准确率。原有详细分析及旧合同由 Git history 保留；当前入口已删除无消费者的 `full_document_contract.py`，旧运行中保存的合同副本不受影响。

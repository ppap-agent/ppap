# audit：检查程序怎么写

新增、修改或转正一个客户的检查程序时按本页做。条款写法、试跑、切换审核的流程见 [docs/rules.md](../../docs/rules.md) 第 7、8 步；哪些文件受保护见仓库根目录 [AGENTS.md](../../AGENTS.md)。

## 检查程序的验收目标

新增、修改或转正检查程序时，按以下目标验收。适用于所有客户和文件类型，不改变条款原意。

- **换原件、换版式仍能检查。** 用原件内容识别文档类型、表格和字段，不依赖样本文件名、固定位置或某一份模板。任何未读完的范围都不能当成已经核对。
- **按字段含义取值，按条款比较。** 从条款要求的文件和字段读取事实；跨文件的值只用于条款明确要求的比对，不替原件补值。只做条款允许的归一化；需要换算时，单位须有原件依据。角色不明、值冲突或单位不明时，不选一个能通过的值。
- **每个结论都能回到原件核对。** 程序按条款判定，模型只负责读取。证据说明来自哪份文件、哪一页或哪张表的哪一处，以及实际读到了什么。读不到不等于原件缺失；证据不足记为判不了，不能当作通过或不通过。原件已证实的独立不符合，不因其他证据不足而抹掉。

验收至少用不同的原件或版式验证受影响的能力。涉及识别时，要从识别入口验证，不能预先指定类型。参考判定只用于事后比较差异，不作为运行输入；不能为提高样本一致率加入例外。

读取时，先用原生结构和明确、可解释的业务知识；只有这些不足以核实条款所需事实时，才用模型补读。只发送必要的原文，重复内容去重，按文件和条款合批，不逐行调用。新增模型读取要有不随明细行数增长的调用上限；后一步确实依赖前一步发现时，才拆成有上限的多步。超出读取范围要说明尚未核实，不得用模型猜测原件缺项或改写已核实的事实。

## 两层：叶子只声明，判定写在共享库

| 位置 | 放什么 | 不放什么 |
|---|---|---|
| 叶子 `src/audit/checkers/<公司>/<叶子>/` | `rules.json`、`checker.py`、`resources.py` 三份文件 | 判定、读取代码，其他文件 |
| 共享库 `src/audit/checker_lib/<公司>/` | 读取类，以及每条条款的判定方法 | 别的公司的实现 |

一类文件对应一个叶子，叶子编号与预置规则的条目一致（如 UAES 控制计划是 `U08`）。

### checker.py

每个叶子都是下面这 8 行，只改第一行说明里的叶子编号：

```python
"""UAES U08 runs the uaes reader implementation for each bound clause."""

from audit.checker_lib.agents import AgentAudit
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return AgentAudit(audit_file, rule, group_context, resource=RESOURCE)
```

不在这里继承 `AgentAudit`，不在运行时生成类，也不给读取类挂方法。

### resources.py

只写一个纯数据的 `RESOURCE`：不 import，不写循环、推导式或函数调用。

```python
RESOURCE = {
    "customer": "UAES",
    "preset_key": "U08",
    "carriers": {".xlsx": "audit.checker_lib.uaes.v1.control_plan:UaesControlPlanRevision3", ...},
    "methods": {"CP:1": "rule_1", "CP:2": "rule_2", ...},
}
```

- `carriers`：原件后缀 → `audit.checker_lib.<本公司>.<模块>:<类>`，只能指向本公司共享库里的类。
- `methods`：条款编号 → 类上的方法名，每条条款一项。
- 可选 `implementation_rules`：读取类自己有条款编号时用。

### rules.json

- `rules[]` 的 `id`、`rule_to_check` 与预置规则逐字一致；`checker_binding` 每条都是 `"checker:create_audit"`。
- `type`、`preset_key`、`customers`、`related_suite_keys`、`directory_coverage` 等照已有叶子填。

## 共享库里的类

- 读取类继承本公司已有的读取类（如 `UaesControlPlanAgent`），或通用的 `audit.checker_lib.excel_agent.ExcelAgent`。
- 每条条款一个方法，用 `audit.checker_lib.excel_agent.rule` 装饰器声明在类上（`@rule("简述")`）。运行时按 `methods` 里的方法名在类的声明里找；没声明的方法不会被执行。
- 写成普通的模块和类：不用 `type(...)` 现场造类，不用 `globals().update` 注入名字，需要什么就 import 什么。
- 规则版本换了（条款编号变了），新建版本子包，如 `checker_lib/uaes/v1/`。新类继承旧版读取类，只加新条款的方法。旧版的类和方法不动：旧语料的人工真值仍用存档的旧叶子（如 `assets/uaes/rulebook/v0.0/m1/checkers`）跑当前的共享库。
- 几个读取类共用的代码放进同一子包的一个模块（如 `uaes/v1/reader.py`），不在每个模块里各抄一份。
- 判「通过」之前，每做一次真正的比较就调用一次 `ctx.record_execution()`（规则上下文记下证据单元格、完成一段扫描或一次检索时会自动计数，只在 Python 里比较的要自己登记）。线上审核会把零核对的「通过」改判为技术错误（`agent.mapping.unproven_pass`）。
- 读图、转写等模型请求一个一个发，不用线程池并发。线上执行环境的模型网关一次只处理一个请求，排队时间也算进单个请求的时限，并发发出的请求会在队尾超时。

## 候选与转正

- 候选叶子放在 `assets/<客户>/rulebook/<版本>/candidates/m<修订>/<公司>/<叶子>/`，和正式叶子同形：同样三份文件，同样的 `checker.py`。
- 候选的判定代码写进共享库，写成新的模块或新的类，不改正式叶子正在用的类和方法。候选的 `resources.py` 指向这些新类。
- 转正：把候选叶子的三份文件复制进 `src/audit/checkers/<公司>/<叶子>/`，候选目录留作历史；再按 docs/rules.md 第 8 步重编预置规则、部署。

## 测试

- 叶子的结构由 `tests/test_predefined_checker_directory.py` 检查。改完叶子先跑它，再跑 `python scripts/compile_v4_presets.py --check`。
- 判定方法用真实样本测，测试放在 `tests/corpus/contracts/tests/`。样本试跑（`corpus_contracts.uaes_samples.run_batch`、`candidate_run`）按线上规则出结论，零核对的「通过」同样显示为技术错误。叶子要用 `audit.standard_suites._load_leaf` 读出，再交给 `agent.execution.audit_runtime.run_audit`（或 `_load_program`）按产品路径执行。不要按文件路径用 `importlib` 加载模块，仓库约定测试会拒绝。
- 只跑受影响的测试。

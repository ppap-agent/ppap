# audit：检查程序怎么写

新增、修改或转正一个客户的检查程序时按本页做。条款写法、试跑、切换审核的流程见 [docs/rules.md](../../docs/rules.md) 第 7、8 步；哪些文件受保护见仓库根目录 [AGENTS.md](../../AGENTS.md)。

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

## 候选与转正

- 候选叶子放在 `assets/<客户>/rulebook/<版本>/candidates/<公司>/<叶子>/`，和正式叶子同形：同样三份文件，同样的 `checker.py`。
- 候选的判定代码写进共享库，写成新的模块或新的类，不改正式叶子正在用的类和方法。候选的 `resources.py` 指向这些新类。
- 转正：把候选叶子的三份文件复制进 `src/audit/checkers/<公司>/<叶子>/`，候选目录留作历史；再按 docs/rules.md 第 8 步重编预置规则、部署。

## 测试

- 叶子的结构由 `tests/test_predefined_checker_directory.py` 检查。改完叶子先跑它，再跑 `python scripts/compile_v4_presets.py --check`。
- 判定方法用真实样本测，测试放在 `tests/corpus/contracts/tests/`。叶子要用 `audit.standard_suites._load_leaf` 读出，再交给 `agent.execution.audit_runtime.run_audit`（或 `_load_program`）按产品路径执行。不要按文件路径用 `importlib` 加载模块，仓库约定测试会拒绝。
- 只跑受影响的测试。

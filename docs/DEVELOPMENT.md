# 开发

普通工作只需要当前 owning Issue、相关源码和最近测试。目标是尽快完成迁移和开发，让客户尽早使用。

## 当前工作组织

剩余工作由四个大 Issue 拥有：#3 engine parity、#7 核心产品闭环、#11 飞书 parity、#15 生产交付/sunset。

Issue 表达验收结果，不表达实施顺序。允许并行开发，也允许一个 PR 同时推进多个 Issue；不要为了依赖图把可以并行的工作串行化。

## 可直接使用的资产

`ppap-agent` GitHub 组织中的仓库内容、配置、文档、测试数据，以及我们可以访问的远程环境，默认已经授权用于本项目开发、迁移、测试和交付，除非产品负责人以后明确说某项不能用。

可以直接读取和迁移现有实现、配置、脚本、fixture、oracle、测试和可访问环境。不要为“这些资产能不能用”增加默认审批或 evidence 流程。

## 配置

应用配置的 executable authority 是 typed Settings 和 [`.env.example`](../.env.example)。

- Secret 只通过本地 gitignored `.env`、GitHub Secrets 或部署 Secret 注入。
- 可选 capability 缺配置只影响该 capability，不阻止无关开发。
- 已有组织内配置可以直接用于迁移/开发；终态配置归本仓 Settings。
- 新增配置项前先确认它代表独立运行决策，而不是可推导内部细节。

## 当前命令

```sh
python tools/stack.py install
python tools/stack.py up
python tools/stack.py verify
python tools/stack.py seed-auth
python tools/stack.py verify-auth
python tools/stack.py verify-uploads

python -m pip install -r requirements.txt
PYTHONPATH=packages:services/api:services/worker python -m unittest discover -s tests/unit -v
```

#15 的终态工程基线会把项目入口收敛到 Python 3.12 + `pyproject.toml + uv.lock`、Ruff、核心 Pyright，以及 Vue 3 + TypeScript + Vite + pnpm lockfile；这些是生产交付验收，不要求迁移第一步先重构所有旧 checker。

## 迁移工作

选择迁移粒度时以**最快形成稳定客户路径且降低终态上下文**为准。一个 package/能力域整体迁入更快时就整体迁入；迁入后立即把 tests/config 归本仓并删除跨仓长期依赖和无消费者代码。

Engine migration 以 [TESTING](TESTING.md) 的固定 baseline parity 为准；不要把旧算法已有 failure 变成迁移 blocker。

## 最短交付路径

当前 Issue → 复用/迁入现成资产 → 跑 baseline/当前测试 → 修新增 regression 或产品缺口 → 必要真实环境验证 → 当前 PR → 尽快打通客户路径。

实施状态只更新 Issue/PR，不另建 roadmap、运行台账或 proof registry。

# 开发

普通工作只需要当前 owning Issue、相关源码和最近测试。Issue 表达验收结果，不表达实施顺序。

组织内资产的默认使用规则见 [AGENTS.md](../AGENTS.md)，这里不重复权限政策。

## 当前配置

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
python tools/stack.py verify-audit

python -m pip install -r requirements.txt
PYTHONPATH=packages:packages/ppap_docops/src:packages/ppap_excel_audit_engine/src:packages/ppap_ocr_adapter/src:packages/ppap_pdf_to_excel/src:packages/ppap_vision/src:services/api:services/worker \
  python -m unittest discover -s tests/unit -v
python -m pytest tests/corpus/v2_assets --ftype excel -q
```

完整 corpus 命令见 [TESTING](TESTING.md)。以上是合入后的真实入口。

## #15 的目标工程工具链

为达到单仓、现代、易维护的生产工程质量，实施时优先收敛为：

- Python 3.12，单一 `pyproject.toml` 项目入口与锁定依赖；
- Ruff 负责格式与 lint；
- 核心 API / product / worker 使用 Pyright；迁入旧 checker 可渐进纳入；
- Web 延续 Vue 3 + TypeScript，迁到 Vite；使用一个带 lockfile 的前端 package manager；
- CI 调用与本地相同的 canonical quality/test/build 命令。

这些是工程实现方向，不是产品 Issue 的固定业务政策；若当前代码出现更简单且同等可维护的方案，可以在实现中调整。

## 迁移工作

选择迁移粒度时以**最快形成稳定客户路径且降低终态上下文**为准。一个 package/能力域整体迁入更快时就整体迁入；迁入后立即把 tests/config 归本仓并删除跨仓长期依赖和无消费者代码。

Engine migration 的具体 baseline 见 #3；飞书 capability baseline 见 #11。不要把旧算法已有 failure 变成迁移 blocker。

## 最短交付路径

当前 Issue → 复用/迁入现成资产 → 跑 baseline/当前测试 → 修新增 regression 或产品缺口 → 必要真实环境验证 → 当前 PR → 尽快打通客户路径。

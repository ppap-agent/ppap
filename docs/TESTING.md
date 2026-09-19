# 测试

测试保护当前行为，不维护第二套产品状态或“证明台账”。

## 原则

- **logic/unit**：完整覆盖稳定规则和数据不变量。
- **integration**：覆盖数据库、对象存储、Worker、授权等关键边界。
- **E2E / external**：只为无法在低层证明的少数用户结果或真实外部效果使用。
- 测试应该断言稳定性质，不冻结当前文件数量、目录 hash、服务清单或已经删除功能的“持续不存在”。
- Git 中的 fixture/oracle 由 Git 版本化；不要再维护一份 hash manifest 或 oracle authority registry。

## 普通 CI

```sh
PYTHONPATH=packages:services/api:services/worker python -m unittest discover -s tests/unit -v
```

GitHub Actions 在 push / pull request 上直接运行这组测试。Core CI 不依赖 LLM、飞书或其它可选外部 provider 的在线状态。

## 本地集成检查

需要当前 isolated-dev 边界时再运行：

```sh
python tools/stack.py up
python tools/stack.py verify
python tools/stack.py verify-auth
python tools/stack.py verify-uploads
python tools/stack.py down
```

这些命令只检查自己实际执行的行为，不映射成 Pxx/Gxx，也不聚合成 Story 状态。

## Corpus / 引擎迁移

#3 使用固定 `ppap-agent` revision 中已有的 V2 4.8、POC grouped、PSW cross-format pytest 和人工 expected。直接 checkout 固定 revision 并运行原生 tests；失败就是迁移输入，不另建 corpus runner。

#6 每次从一个真实失败测试出发，只迁入让当前支持用例通过所需的最小代码。

## 外部系统

LLM 和飞书 readiness workflow 仅在需要排查相应 integration 时手动运行。涉及真实非幂等外部写操作时，测试应检查目标、失败语义和必要 readback；无外部效果的路径不增加 mutation/evidence state machine。

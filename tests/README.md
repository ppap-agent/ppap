# Tests

测试策略见 [docs/TESTING.md](../docs/TESTING.md)。本目录只说明直接可运行的入口，不维护另一套验收状态。

```sh
PYTHONPATH=packages:services/api:services/worker python -m unittest discover -s tests/unit -v
```

需要本地真实 PostgreSQL / 对象存储边界时：

```sh
python tools/stack.py up
python tools/stack.py verify
python tools/stack.py verify-auth
python tools/stack.py verify-uploads
python tools/stack.py down
```

`unit/` 覆盖当前代码的不变量和回归。外部 provider 或浏览器 E2E 只有在对应当前功能需要时才添加；不要先建设通用 live gate、PROVEN/NOT_PROVEN 报告或 evidence registry。

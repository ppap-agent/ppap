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

#3 的受控 corpus 不在本目录。固定 revision 与原生 pytest 命令见 [TESTING](../docs/TESTING.md) 和 [ppap-agent pytest](../.github/workflows/ppap-agent-pytest.yml)。不要把 `ppap-agent` `assets/` 提交到本仓。

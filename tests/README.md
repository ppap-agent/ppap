# Tests

用户验收合同在 [STORIES](../docs/STORIES.md)，证明范围和聚合规则在 [TESTING](../docs/TESTING.md)，环境和实际命令在 [DEVELOPMENT](../docs/DEVELOPMENT.md)。这里不复制另一套验收政策。

当前已存在：`unit/test_live.py` 检验验收工具的误通过/误拒绝和安全失败；`live/` 保存工具依赖与 oracle 示例；`tools/live.py` 执行真实端点探针和首批浏览器子证明。

```sh
PYTHONPATH=packages:services/api:services/worker python -m unittest discover -s tests/unit -v
python tools/stack.py verify   # requires a running isolated-dev stack; not a Story
```

`unit/` 检验验收工具误通过/误拒绝，以及 #2 的 settings/HTTP 表面。`live/` 保存工具依赖与 oracle 示例。产品审核、授权与 corpus 仍由对应 Issues 交付。

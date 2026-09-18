# Tests

用户验收合同在 [STORIES](../docs/STORIES.md)，证明范围和聚合规则在 [TESTING](../docs/TESTING.md)，环境和实际命令在 [DEVELOPMENT](../docs/DEVELOPMENT.md)。这里不复制另一套验收政策。

当前已存在：`unit/test_live.py` 检验验收工具的误通过/误拒绝和安全失败；`live/` 保存工具依赖与 oracle 示例；`tools/live.py` 执行真实端点探针和首批浏览器子证明。

```sh
python -m unittest discover -s tests/unit -v
```

产品本身尚未实现。对应 Issues 必须随实现交付领域、数据库/Worker、引擎 corpus、迁移与安全测试；不能把工具自测当成产品回归。

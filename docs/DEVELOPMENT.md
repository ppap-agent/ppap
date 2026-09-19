# 开发

普通工作只需要当前 Issue、相关源码和最近测试。不要先读历史调查、旧路线图或关闭的 PR。

## 配置

应用配置的 executable authority 是 typed Settings 和 [`.env.example`](../.env.example)。API 与 Worker 复用同一语义；不要在文档里维护第二份默认值表。

规则：

- Secret 只通过本地 gitignored `.env`、GitHub Secrets 或部署 Secret 注入。
- 可选 capability 缺配置只影响该 capability，不阻止 Core 启动和无关测试。
- 生产配置显式提供实际 endpoint/credentials，不依赖 isolated-dev defaults。
- 新增配置项前先确认它代表独立运行决策，而不是可从现有 authority 推导的内部细节。

## isolated-dev

```sh
python tools/stack.py install
python tools/stack.py up
python tools/stack.py verify
python tools/stack.py seed-auth
python tools/stack.py verify-auth
python tools/stack.py verify-uploads
python tools/stack.py down
```

`stack.py` 启动本地 PostgreSQL、对象存储、API、Worker 和 Web。`verify-*` 是直接集成检查，不是产品 Story 状态系统。

## 测试

```sh
python -m pip install -r requirements.txt
PYTHONPATH=packages:services/api:services/worker python -m unittest discover -s tests/unit -v
```

测试层级和 corpus 规则见 [TESTING](TESTING.md)。

## 外部 integrations

[LLM readiness](../.github/workflows/llm-readiness.yml) 与 [Feishu readiness](../.github/workflows/feishu-readiness.yml) 是手动诊断 workflow。普通 push/PR CI 不依赖它们。

如果当前任务涉及真实 OAuth、Drive/Docs、Partner AI 或其它非幂等外部写操作，再为那个边界做最小 integration test/readback。不要为了未来可能使用而提前增加 provider abstraction、Secret 或重试状态机。

## 迁移工作

只有迁移 Issue 才读取 [INVENTORY](INVENTORY.md) / [MIGRATION](MIGRATION.md) 和来源仓。固定来源 Git revision 后优先运行来源仓已有 tests（#3：[ppap-agent pytest](../.github/workflows/ppap-agent-pytest.yml)）；先得到真实失败，再搬最少代码。

## 最短交付路径

当前 Issue → 局部代码 → 最近测试 → 必要的边界测试 → 当前 PR。实施状态只更新 Issue/PR，不另建运行台账。

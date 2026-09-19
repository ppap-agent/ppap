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

#3 固定 `ppap-agent@53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9`。GitHub Actions [ppap-agent pytest](../.github/workflows/ppap-agent-pytest.yml) 只 checkout 该 revision、安装 uv、跑已有 pytest。结果是普通 PASS / FAIL / 未执行；失败就是 #6 的迁移输入。不要另建 corpus runner、evidence JSON、hash manifest 或 oracle registry。

本地（不要把 `assets/` 复制进本仓）：

```sh
git clone https://github.com/ppap-agent/ppap-agent.git
git -C ppap-agent checkout 53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9
cd ppap-agent
uv sync --frozen --dev --package ppap-agent

SUITE_CSV="assets/全量测试 - 4.8文件测试反馈（云端） - 4.8（云端）.csv"
uv run --frozen --package ppap-agent pytest ppap_agent/tests/v2_assets/test_v2_assets.py --ftype excel -q --v2-dataset-root assets/v2.0plus --v2-expected-csv "$SUITE_CSV"
uv run --frozen --package ppap-agent pytest ppap_agent/tests/v2_assets/test_v2_assets.py --ftype pdf -q --v2-dataset-root assets/v2.0plus --v2-expected-csv "$SUITE_CSV"
uv run --frozen --package ppap-agent pytest ppap_agent/tests/v2_assets/test_v2_assets.py --ftype scanned -q --v2-dataset-root assets/v2.0plus --v2-expected-csv "$SUITE_CSV"
uv run --frozen --package ppap-agent pytest ppap_agent/tests/v3_poc/test_poc_assets_grouped.py -q
PYTHONPATH=ppap_agent uv run --frozen --package ppap-agent pytest ppap_agent/tests/unit/test_psw_cross_format_regression.py ppap_agent/tests/integration/test_psw_cross_format_audit.py -q
```

五个入口都要跑。DOC 需要本机 LibreOffice；缺依赖记未执行/失败，不能从集合里拿掉后宣称支持。CI 读私有来源仓需要 `PPAP_GH_TOKEN`（对 `ppap-agent/ppap-agent` contents:read）；没有这个 Secret 时 checkout 失败，五个入口都是未执行。

#6 每次从一个真实失败测试出发，只迁入让当前支持用例通过所需的最小代码。

## 外部系统

LLM 和飞书 readiness workflow 仅在需要排查相应 integration 时手动运行。涉及真实非幂等外部写操作时，测试应检查目标、失败语义和必要 readback；无外部效果的路径不增加 mutation/evidence state machine。

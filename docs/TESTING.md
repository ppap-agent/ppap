# 测试

测试保护当前行为，不维护第二套产品状态。

迁移 baseline 的具体 Git revision 和当前观察结果只由 owning Issue 保存：
- engine/corpus parity：[#3](https://github.com/ppap-agent/ppap/issues/3)
- 飞书 capability parity：[#11](https://github.com/ppap-agent/ppap/issues/11)

迁移完成后，本仓测试成为 current authority，不继续复制 legacy baseline 到长期文档。

## 测试层级

- **logic / unit**：稳定规则和数据不变量。
- **corpus regression**：完整 engine parity；baseline 原有 failure 不属于 migration regression。
- **integration**：数据库、对象存储、Worker、授权和必要外部接口。Deep 模式（happy path / runtime unavailable / revision mismatch / fencing）在 `tests/unit/test_deep_mode.py`，不要求 live gVisor。gVisor guest 与 process runtime 的确定性逻辑在 `tests/unit/test_deep_runtime.py`。Live Deep（isolated-dev + host Controller + gVisor）不进入默认 unit CI：需要本机 Docker runtime `ppap-gvisor-verify`，命令见下方。
- **E2E**：少量完整客户路径。#7 覆盖 Web 审核闭环；#11 覆盖飞书来源进入同一产品闭环。#57 把同一条浏览器路径延伸到 PC 导航、结果/依据/预览、历史轮次、复核和用量。飞书 Drive 导入与文档关联文件的确定性回归在 `tests/unit/test_feishu_drive.py` 与 `tests/unit/test_feishu_doc_links.py`（mocked Open API）。docs-addon 宿主登录到普通 round 的确定性回归在 `tests/unit/test_feishu_docs_addon.py`（mocked host code + Open API）。客户路径 `/addon`、`/audit-files` 与 `/usage` 的静态入口在 `tests/unit/test_web_server.py`。Web 只调 Product API 的边界在 `tests/unit/test_web_boundary.py`。InsForge overlay 与 Custom Compute 规格在 `tests/unit/test_insforge_runtime.py`（不要求 live InsForge）。

规则排列组合留在低层，不复制到 E2E。

## 当前可运行入口

Web 合并契约分两段。类型、生产构建和前端边界不依赖 Docker；代表性浏览器路径仍是 isolated-dev 上的 `tests.e2e.test_web_audit`。CI 里它们分别是 `Web contract` 与 `e2e`。`push` 和 `pull_request` 使用不同的 concurrency group，同一 head 不会互相取消。corpus 仍会跑，不因为一次 Web 改动被跳过。规则导入、草稿提交和复核保存的合法转移在 `pnpm test`，E2E 不复制这些排列组合。`pnpm lint:design` 对 feature 代码硬失败：原始布局、原始控件、className、inline style、直接引用样式后端、feature CSS、设计字面量。对比度在 `pnpm test`。桌面/平板/手机、键盘、焦点、触控目标和减少动效的样式契约在同一条 lint；浏览器里的键盘、焦点、视口和减少动效在 `tests.e2e.test_ui_a11y`。`src/style.css` 与 `src/ui/foundation.css` 仍是待删除的旧样式表，除此之外的 CSS 只能留在 `src/ui/system`。

```sh
cd apps/web
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test
pnpm lint:design
pnpm build
cd ../..
python -m pip install -e ".[dev]"
python -m unittest tests.unit.test_web_boundary tests.unit.test_web_server tests.unit.test_product_experience -v
```

```sh
python -m pip install -e ".[dev]"
git show --format= --check HEAD -- . ':!assets' ':!tests/fixtures'
python -m compileall -q packages services tests
python -m unittest discover -s tests/unit -v
python -m pytest tests/corpus/v2_assets --ftype excel -q
python -m pytest tests/corpus/v2_assets --ftype pdf -q
python -m pytest tests/corpus/v2_assets --ftype scanned -q
python -m pytest tests/corpus/v3_poc -q
python -m pytest tests/corpus/test_psw_cross_format_regression.py tests/corpus/test_psw_cross_format_audit.py -q

docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
python -m unittest tests.schema.test_upgrade tests.schema.test_rule_parse -v
docker compose -f deploy/isolated-dev/compose.yaml down -v
# schema tests talk to postgres with native psql on 127.0.0.1

export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker buildx bake
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example up -d --wait
python -m unittest tests.deploy.test_runtime tests.deploy.test_backup -v
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example down -v --remove-orphans
```

PSW 的 `.doc` 用例需要本机 LibreOffice，以及 Linux 上可执行的 `ppap-local-converter-sandbox`（由 `packages/ppap_docops/native/legacy_doc_sandbox.c` 编译）。`.doc` 转换是默认行为，没有开关变量；转换器或沙箱缺失时该项记 ERR，不跳过格式。需要 Paddle 的 PDF 转换优先使用 `tests/fixtures/ocr_cache`；未命中时再读 `PADDLE_OCR_URL`。人工 expected 以 corpus 内 CSV/XLSX 为准，不要改它来迎合实现。

#7 的代表性浏览器 E2E 覆盖待审+参考、提交、离开后回访、打开当前 round 冻结原件（或看到 locator-gap）、原件预览、复核、显式重审，以及 PC 导航到用量和审核文件历史：

```sh
python -m pip install -e ".[dev,e2e]"
python -m playwright install chromium
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker buildx bake
docker compose -f deploy/isolated-dev/compose.yaml up -d --wait
python -m unittest tests.e2e.test_web_audit tests.e2e.test_ui_a11y tests.e2e.test_rules tests.e2e.test_rule_interactions -v
docker compose -f deploy/isolated-dev/compose.yaml down -v
```

Live Deep（本机已注册 `ppap-gvisor-verify`，且 isolated-dev 已起来）把 Controller 放在 host，Worker 只挂 UDS：

```sh
mkdir -p /tmp/ppap-deep
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml up --build -d --wait
PPAP_DEEP_RUNTIME=gvisor PPAP_DEEP_CONTROLLER_SOCKET=/tmp/ppap-deep/controller.sock python -m ppap_deep
# 另一个 shell：
PPAP_DEEP_LIVE=1 python -m unittest tests.live.test_deep_isolated -v
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml down -v --remove-orphans
```

Controller 进程环境可以另给 `PPAP_DEEP_RUNTIME=gvisor`、`PPAP_DEEP_DOCKER_RUNTIME` / `PPAP_DEEP_GUEST_IMAGE`；不要把这些写进 Worker Settings。默认 process runtime 不需要 Docker。Silo 只在 `deep-live.yaml` overlay 才映射 `127.0.0.1:9000`，默认 isolated-dev 仍不发布对象存储。可选模型 grant 用 host 的 `PPAP_DEEP_GROK_BIN` 与 `PPAP_DEEP_GROK_CONFIG`，不要把 `~/.grok` 挂进 Worker。

组织内测试资产和可访问环境按 [AGENTS.md](../AGENTS.md) 的默认规则直接使用。

`tests/unit/test_rules.py` 通过真实 Excel/Word 提取与模拟外部 LLM 验证规则解析，再通过 Product API / Worker 验证受理、预览、发布、版本不变与来源授权。`tests/unit/test_rule_ai_client.py` 验证每调用超时、零自动重试及错误日志脱敏。`tests/schema/test_rule_parse.py` 在独立临时 PostgreSQL 数据库验证并发重复受理、未知租约、迟到完成和来源权限读回。`tests/e2e/test_rules.py` 使用结构化规则文件走真实上传、人工编辑、发布和重访；`tests/e2e/test_rule_interactions.py` 在浏览器 API 边界控制迟到响应，验证整页操作互斥、刷新只读及显式重跑。这些测试不调用外部模型服务。

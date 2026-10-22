# 测试

测试保护当前行为，不维护第二套产品状态。

迁移 baseline 的具体 Git revision 和当前观察结果只由 owning Issue 保存：
- engine/corpus parity：[#3](https://github.com/ppap-agent/ppap/issues/3)
- 飞书 capability parity：[#11](https://github.com/ppap-agent/ppap/issues/11)

迁移完成后，本仓测试成为 current authority，不继续复制 legacy baseline 到长期文档。

人工 CSV 是测试期望的唯一数据源，但标注本身可以复核。争议样本先确认测试实际映射的文件及其摘要，再按原始规则和完整相关页面中的可定位事实裁决；更正期望需保留依据，不能以模型结论、一次重跑通过或放宽 baseline exception 代替复核。PSW 规则 3、5、8～10 的视觉路径只从模型读取签字／印章／勾选事实，由程序执行固定条件；空白盖章栏不等于实际盖章，缺少必需事实不能通过。这些判定输入与可选 locator／摘录分开，不以可选证据完整性改变已有 PASS/NG。

PSW 第5条只要求授权供方代表签字和盖章同时存在，不增加必须手写、鉴真或公司章类型条件；印刷姓名等其他字段不能代替签字字段。两项事实均为 true 才 PASS；任一明确 false 为 NG；没有明确 false 但仍有未知事实为 UNABLE，调用／JSON 协议失败仍为 ERR。DOCX 的原生文字及 OOXML 位置随原内嵌图片进入同一视觉请求，成功判定保留 structure／visual 两种来源；不要求渲染或额外模型调用。`test_psw_docx.py` 和 `test_psw_pdf_hybrid.py` 经公开审核入口验证原件输入、三值组合和不采信模型最终 verdict；只控制外部模型／转换边界，不证明真实识别稳定性或已解决历史 PSW 波动。

PSW 混合审核分别验证三类结果：有效业务事实不满足规则为 NG；可解析但关键事实缺失或无法辨认为 UNABLE；转换、调用、JSON／输出协议失败为 ERR。明确不存在的字段或未勾选不是“无法辨认”，无关分支未知也不抹掉已充分决定的 PASS/NG。PDF 与 DOCX 共用此视觉结果合同；受控模型／转换响应的测试不证明真实识别质量。

原件视觉回归须检查解码后的图像内容及所选页面，不能只检查 data URL 前缀或审核状态。图像依赖最低为 Pillow 10.2.0：10.1.0 的 Mac ARM wheel 存在 JPEG 编码损坏，上游已在 10.2.0 修复（[Pillow #7628](https://github.com/python-pillow/Pillow/issues/7628)）；原件测试允许正常 JPEG 有损压缩误差，不要求输出字节与原件相同。

检具 PDF 的转换失败回退由 `test_checking_fixture_pdf.py` 经公开 `.check()` 验证：可读文件缺少必需部分是 NG，无法读取、空文件或解析损坏是执行错误；不将解析失败吞成“未找到内容”，也不使用模型替代这些文件边界检查。第三项检查附件与结论的关系；缺少报告或附件部分为业务 NG，实际调用／输出协议失败保留 ERR。

关联 PDF 的提取回归只在外部转换边界提供受控产物，真实工作簿加载、行提取与跨文件判定保持运行。`test_cross_file_references.py` 经公开 `run_audit_engine` 验证：已提供的关联资料因转换或读取失败而不能使用时，受影响规则为脱敏 ERR；可读文件缺列或未提供业务资料仍由审核器判 NG，不改独立规则的结果。类型化和属性式上下文均须保留 `unavailable_files`。共享 `load_workbook_auto(cleanup=True)` 在加载成功或异常时都清理转换临时产物；`cleanup=False` 将清理责任留给调用方，原始输入文件不属于临时产物。两种清理模式均由转换管道自己的 owner tests 验证，不复制旧 V2 私有 loader 的调用约束。

PDF 委托器通过既有执行结束钩子主动关闭委托工作簿并清理转换临时文件，不依赖析构或循环 GC。异常分支也须在返回前完成清理；原件字节保持不变。

## 测试层级

- **logic / unit**：稳定规则和数据不变量。
- **corpus regression**：完整 engine parity；baseline 原有 failure 不属于 migration regression。
- **integration**：数据库、对象存储、Worker、授权和必要外部接口。Deep 的受理、版本、fencing 与传输边界由 `tests/integration/product/test_deep_mode.py` 验证；语义 guest 与容器权限/模型 grant 的确定性逻辑由 `packages/ppap_deep/tests/test_deep_runtime.py`、`packages/ppap_deep/tests/test_deep_semantic.py`、`packages/ppap_deep/tests/test_deep_isolation.py` 验证。脚本化 runtime 只证明协议，不证明真实审核。`tests/live/test_deep_isolated.py` 使用独立 `ppap-deep` 容器、真实 Grok 和模型，但仅是玩具 XLSX 的真实模型协议 smoke，不是客户 A1–A12 样例验收；它不进入默认 merge contract。

`packages/ppap_deep/tests/test_deep_client.py` 使用真实 UDS 验证未送达请求的显式恢复，以及分片响应、发送背压中的总时限与取消。外部 peer 受控；不调用模型，也不证明 provider 已取消或执行域已清理。总时限从调用开始计算，帧内读写不能重置预算；已发送请求超时不等于未执行，客户端不自动重发。

`tests/integration/runtime/test_runtime.py` 在同 SHA 的交付 Worker 镜像中运行 `deep_container_cases.py`，验证公开 `ContainerRuntime.run` 的排队超时、取消和执行位释放。真实 Linux、构造器、锁及文件准备/清理不替换；外部 guest 进程受控，版本探针使用测试程序，不调用 Grok 或模型，不证明语义质量或强隔离。`DeepExecutionQueue.tla` 只检查一个占用者与一个等待者：即使占用者不结束，到期/取消仍能响应；正向配置检查允许时钟和等待线程持续获得调度时的终结性质，`-unbounded` 配置保留响应依赖占用者释放的反例。它不证明毫秒级时限、运行中取消、整个进程树清理或完整系统 refinement。

同一 native 入口还通过真实本地 gateway（Unix grant 控制／loopback HTTP）验证执行包读回、同 execution 并发合并、已消费后失败不重放、未知 provider 完成状态以及显式新 submission。仅 HTTPS provider 与 guest 进程受控；原生锁、journal、attempt、gateway 记账及目录不替换。runtime 重建与重复 recover 使用同一保留状态、在上一 guest 已终结后执行，不等于进程中途崩溃、丢卷或跨节点恢复。journal 输出仍须经过业务验证，不作为实际审核质量或报告完成证明。`DeepExecutionReplay.tla` 是两个调用共享一个冻结 execution 的有限 Safety 检查；race／failure／result 配置分别保留不加锁、重放失败 attempt、忽略既存输出的反例，不证明完整 refinement、最终完成或外部平台幂等。

缓存用例在同一入口执行真实 XLSX → `SemanticRuntime` → `ContainerRuntime` → guest 解析、计划／结果验证和产品转换。进程替身在测试进程内调用真实 guest，模型 StageRunner／HTTPS 和对象存储为外部边界替身，不证明实际 Grok、S3 耐久性或 guest 权限隔离。两份目标共用规则，新 submission 复用有效 snapshot／plan 但重新审核；缓存中的无害解析注记区分实际复用与重新解析，错误摘要／计划 revision 分别重新解析／编译。原 submission 再读不执行新审核，gateway 记录的受控请求数与用量同步核对。这是执行与缓存合同，不是客户判定质量或生产费用证据；现有单 execution 的 Replay 模型不覆盖跨 submission 缓存。
- **E2E**：少量关键客户路径和跨栈边界，覆盖当前上传、历史轮次、原件与人工复核；各入口区分受控交互和真实服务证据，不据此宣称全部客户流程完成。飞书 Drive 导入与文档关联文件的确定性回归在 `tests/integration/product/test_feishu_drive.py` 与 `services/api/tests/test_feishu_doc_links.py`（mocked Open API）。docs-addon 宿主登录到普通 round 的确定性回归在 `tests/integration/product/test_feishu_docs_addon.py`（mocked host code + Open API）。客户路径 `/addon`、`/audit-files` 与 `/usage` 由 nginx 自己检查：`nginx -t` 加上容器内的路由请求，在 `tests/repository/test_web_server.py`。Web 只调 Product API 的仓库边界、helper、路由、reducer 与 public-config 都由 `apps/web` 的 `pnpm test` 拥有；跨栈黑盒浏览器行为由 Python Playwright（`tests.e2e`）拥有。InsForge overlay 的 **static config contract** 由 `node deploy/insforge/verify-config.mjs` 拥有：Compose 语法与插值交给 `docker compose config`，再检查 Compose 无法知道的少量 PPAP invariant。**live native lifecycle** 是 `bash deploy/insforge/smoke-local.sh`（`@insforge/cli local start/status/db query/stop`），只在 InsForge 相关 path 变化、手动 `workflow_dispatch` 或每周 schedule 上运行。

规则排列组合留在低层，不复制到 E2E。

`tests/e2e/test_ui_a11y.py` 从当前工作台与审核要求入口验证键盘导航、单一主标题、窄屏，以及原件侧栏和草稿确认弹窗的焦点约束与取消返回；API fixtures 只用于受控 UI 验证，不证明真实登录、发布或数据持久化。

规则导入的出处来自固定文件版本的原文字段，不从“待核对”等展示文案推断。`test_rule_sources.py` 与 Product `test_rules.py` 验证真实位置、摘录和发布校验；`test_rule_interactions.py` 验证旧草稿仅补充可唯一匹配的出处、不重新提取或发布，以及原文选择、核对计数和保存门槛一致。`test_web_rules.py` 通过真实 API、PostgreSQL、对象存储与 Worker 验证 JSON 及三份 POC 原始 DOCX 的上传、核对、保存和全新会话重开，检查用户确认的 9／7／11 条原文与顺序、核对状态、来源版本及下载字节。结构清晰的 DOCX 规则表直接提取原文，不要求模型调用；`test_control_plan_rule_assets.py` 保护控制计划原始资产及本地 11 条解析。上述隔离栈测试不证明真实飞书登录或全部 POC 在线审核质量。

同一冻结原件的出处目录迟到且仍支持当前引用时，不清除版本确认；出处有效性变化或用户编辑仍使确认失效。受控测试在确认后显式交付目录响应，不用延时或加长超时代替此边界。

失败轮次的新轮准备由 `test_rule_interactions.py` 覆盖正常、丢失／无效回执、错身份、刷新恢复与离开后的晚到响应，以及多类型／同类型不同规则在编辑和重新读取后的绑定保留；迟到响应先等待响应完成与渲染边界，再检查未发生导航。`test_audit_flow.py` 使用独立 PostgreSQL 验证草稿初始输入、并发同身份重放、撤权、事务回滚及不新增工作／用量；`test_audit_tasks.py` 验证 V3 终态不能绕入旧式重审入口。`RoundPreparation` 是单次准备、两个请求身份、三个导航代次的有限安全模型；数据库去重与授权是外部假设，不证明完整审核、跨窗口原子性或最终网络恢复。准备步骤不改变 `GreenfieldCheckStart` 中的受理计数。

`test_audit_flow.py` 的 Standard 结果写回故障用例通过公开受理、真实 Worker／PostgreSQL 和公开 Round 读回验证：首次 findings 写入事务回滚不能被误归为审核器异常，再提交伪 ERR。完成事务在审核器异常捕获之外；保存失败向调用者传播，原未完成 attempt 不被伪造终结，不自动创建新轮次或消费。此用例不证明持久模型结果缓存、provider 去重或完整技术恢复。

## Runner capability

CI 依赖 capability，而不是 Ubuntu/Debian 或 `sudo`：

- **user-space**：workflow 可以安装 uv/Python、Node、Corepack/pnpm、项目依赖和 Playwright。
- **Docker**：需要启动容器或 Buildx 的 job（service modules / schema / Corpus PSW / Product integration / InsForge lifecycle）要求 `command -v docker`、`docker info`、`docker compose version`、`docker buildx version` 已经可用。缺任一能力时 fail-fast：`runner does not satisfy ppap-docker capability`。workflow 不安装 Docker、不启动 daemon、不 chmod socket、不修改 iptables。
- **schema**：使用 isolated-dev PostgreSQL 容器内的 `psql`（`docker compose exec -T postgres psql`），不要求 host `psql`。
- **PSW `.doc`**：从已构建的 `ppap-worker` 派生一次性测试镜像，通过 build context COPY tests/corpus/fixtures（不 bind-mount 仓库路径），并只安装 pytest。生产 worker 镜像不变，也不在 host 上 apt-install LibreOffice 或 gcc。
- **Compose 路径**：现有 Compose stack 仍会把仓库内 `migrations/` bind-mount 进容器。因此 service modules / schema / Product integration / InsForge lifecycle 需要 Docker daemon 能看到 runner 使用的同一份仓库路径。这是既有 Compose 生命周期，不新增 host-workspace bind mount。
- **Compose isolation**：CI 为 production、isolated-dev E2E 和 schema 使用 `ppap-ci-<purpose>-<run_id>-<run_attempt>` 作为 Compose project name，避免默认目录名互相污染。cleanup 在独立 `if: always()` step；失败时先 dump `compose ps -a` 和对应 stack 的 service logs。
- **Host PostgreSQL isolation**：独立数据库测试设置 `PPAP_TEST_POSTGRES_PORT=0` 让 Docker 分配端口，再从 `docker compose port postgres 5432` 取得实际端口并传给 pytest 的 `PGPORT`。API/product fixtures 会清空其目标库，只能指向本次临时数据库，不能使用正在开发的共享库；普通开发默认端口仍为 5432。容器之间继续使用 PostgreSQL 的 5432。
- **非 Docker job**：Web、无 PostgreSQL 的 Python modules 和普通 Corpus 不要求 Docker。Repository contract 需要 Docker：`docker compose config`、`docker buildx bake --check` / `--print`、nginx 路由、以及 Kustomize / kubeconform 镜像。GitHub Actions 语法由固定版本的 `actionlint` 1.7.7 检查。
- **InsForge static config**：只需要 Docker Compose CLI（`docker compose config`）。不需要 Docker daemon 或 Buildx，也不启动容器。CLI 缺失时由 `verify-config.mjs` 给出明确错误。
- **InsForge live lifecycle**：需要 `local start` 使用的 runtime Docker capability（CI 用 `assert-docker` fail-fast）。

## 当前可运行入口

Single-owner Python tests 与 production owner co-locate：`packages/*/tests` 或 `services/*/tests`。根 `tests/` 只承载 system-level authority：`repository`、`schema`、`corpus`、`integration`、`e2e`、`live`。CI 直接消费这些物理 owner，不维护第二套 test manifest。pytest 不以 `pythonpath` 注入仓库根；production import 只来自 uv workspace 安装后的 packaging metadata。每个 production owner 使用统一 `src/<import_package>/` layout。

默认 merge contract 以 **required** groups 为准，不把“是否 deterministic”当成入 Gate 的条件。默认 `Tests` workflow 每个 PR 都运行：

- **Web**：`apps/web` typecheck / test / lint / build，以及 `tests/e2e/test_*_preview.py` 的受控 UI 回归。CI 启动本候选的显式预览服务，端口占用时由 Vite 选取空闲端口，并读取实际 loopback 监听地址；未发现测试或浏览器执行失败均使此 required job 失败。预览测试只证明 UI/DTO 交互，不证明真实审核、收费或飞书外部效果。
- **Repository contract**：`tests/repository`，加上 `docker compose config`、`docker buildx bake --check` / `--print`、固定的 `actionlint`、canonical TKE Kustomize render 的 kubeconform，以及 InsForge static config contract。Python ruff lint、format 和 mypy 也在这个 job 里，不另开 workflow。TKE CI 与 `apply-current.sh` 共用 `deploy/tke/render-current.sh`；edition proxy ConfigMap 由 `configMapGenerator` 从唯一的 `edition-proxy.conf` 生成。pytest 只保留原生工具不知道的 PPAP rendered-object policy。
- **Python modules**：CI 发现每一个含 `test_*.py` 的 `packages/*/tests` 和 `services/*/tests`，各自一个 pytest 进程。API 与 Worker 不共享进程。需要 PostgreSQL 的只有 `services/*/tests`。纯 Corpus contract tests 位于 `packages/ppap_corpus_contracts/tests`，由同一物理 owner discovery 自动纳入。
- **Distribution closure**：对每个 production workspace member 单独构建 wheel 和其声明的 workspace transitive wheels；在 `/tmp` clean venv 中显式安装这些 wheels，并用 `uv.lock` 约束 registry dependency 版本。从非仓库 cwd import 后再运行该 owner tests。这个 job 不执行 root `uv sync`、不使用 editable install、`PYTHONPATH` 或 `sys.path` bootstrap。删除 `ppap-product-store → ppap-core` 的真实 dependency declaration 有单独 mutation proof，必须使 isolated import fail closed。
- **Schema**：`tests/schema`，继续使用 real PostgreSQL。Empty、previous、重复 apply、`schema_migrations` 顺序和故意失败 migration 都通过 production `migrations/apply.sh` runner 验证；numbered filenames 是唯一 ordering authority。
- **Corpus**：Excel / PDF / scanned / Pairs / V3 / PSW format matrix；Pairs 以 `tests/corpus/pairs`、PSW 以 `tests/corpus/psw` 作为物理 suite authority，新 `test_*.py` 无需修改 workflow；PSW 继续使用 purpose-built worker-derived container。Corpus 是 CI Gate 的 required child：cache miss 时可以调用真实 OCR / model，这不把它变成 live 或 optional。默认 V3 dataset / expected XLSX 缺失或解析后无有效 case 时 fail closed，不 skip。进入 required V3 的 case 必须至少有一条人工 PASS/NG expectation；零断言 case fail closed。V2 每个 required variant 在执行前对账 oracle 选中的全部 case 与真实 asset；任一缺失或歧义映射失败，不跳过部分样本。V2 baseline exception 与 PDF↔Excel Pairs 共用同一个 `ExecutionOutcome`（status / execution_class / 可选 diagnostic_code）；比较整个 structured outcome，不把任意 ERR 当成同一行为。已知 mismatch 消失则要求清理 record。V2 PDF↔Excel pairs 按 oracle 文件 stem 自动配对，在 pair+rule 粒度比较；已知格式分歧写入 structured baseline，不是第二套 human oracle。新的、改变的或已愈合的 divergence 失败；aggregate 一致性百分比不是 merge gate。配对歧义或发现不到任何 pair 时 fail closed。Excel / PDF / scanned 不重复执行 pair suite。V2 对照行只从 JSON 报告读取：缺少 JSON，或某一行缺字段，就 fail closed；不恢复旧 baseline 脚本。
- **Product integration**：`tests/integration/product` + canonical single bake + `tests/integration/runtime` + Python Playwright `tests/e2e`。V3 POC oracle 还走 API 显式规则版本和 Worker，并写出同源 JSON／Markdown 报告；不调用旧 analysis API，也不使用 `matchedRuleId`。
- **Container build authority**：`docker-bake.hcl` 唯一拥有 API / Worker / Web build graph。Compose 只消费 images；Repository contract 从 Bake/Compose native rendered output 核对 image identity 与无 `build` graph，Integration 对同一 `PPAP_BUILD_SHA` 只 Bake 一次并复用。
- **CI Gate**：不运行测试，只汇总以上 required groups（包括 Distribution closure）；任一 required group failed / cancelled / skipped 时 Gate 失败。

外部依赖本身不改变 test ownership，也不决定测试是否 required。`tests/live` 表示不进入默认 merge contract、需要显式执行的真实环境验证，不是“所有带外部依赖的测试”。Root pytest 在保留 pytest 默认递归排除项的基础上额外将 basename `live` 加入 `norecursedirs`，因此裸 pytest recursion 不会意外收集 `tests/live`；显式指定 `tests/live` 目录或具体文件仍会收集并执行。InsForge native live lifecycle 同样不进入默认 `CI Gate`。LLM readiness 与 Feishu readiness 是真实 endpoint / credential probe：保留 `workflow_dispatch`，并每周 schedule 一次；它们不进入默认 PR `CI Gate`，也不复制产品业务测试。当前不做 git-diff/path-aware skipping；required groups 每个 PR 都执行。

`tests/integration/product/test_v3_worker_oracle.py` 在 pytest 的临时输出目录写入 `v3_worker_oracle.json` 和 `.md`，可用 `--basetemp` 指定本次独立临时目录。报告保留逐规则预期／实际、人工原因、审核说明及 Round／Job 引用，分别汇总设置失败、执行失败、缺规则和状态不符。`passed_rules`／严格匹配率包括“实际 NG 与预期 NG 相符”，不是客户文件通过率，也不是验收门禁；原有逐规则断言不变。该集成测试仍用受控对象存储和检查器声明规则，不证明原始 DOCX 的完整在线链路。完整 Live 另由 #152 验收，不能只靠报告汇总宣称完成。

V2 Standard round 的失败日志保留公开 finding 的判定说明：只采用唯一 `item_key` 关联且状态一致的 finding；无法关联时不猜测原因，执行错误仍可使用原轮次说明。这只补充诊断信息，不改变逐条比对结果或验收门槛。

V2 单文件回归也保留实际业务结果的公开 `suggestions`，最多 1024 字符并按单行 JSON 字符串转义，供 required corpus 的失败说明使用。说明不参与 outcome 或 baseline equality；ERR 仍只使用已有稳定 diagnostic code，不新增异常原文。未返回业务说明时不编造原因，也不据一次重跑通过认定模型判定波动已修复。

### 显式 V3 POC Live

`V3 POC live acceptance` 只接受 `workflow_dispatch`，不进入默认 `CI Gate`。它用当前 SHA 的 API／Worker 镜像和独立 PostgreSQL／S3，凭 Secrets 调用真实模型／转换服务；收费和飞书通知关闭。执行前核对两个镜像的 revision，不能让现网旧 Worker 消费候选 API 创建的验收任务。

独立栈可通过 `deploy/isolated-dev/compose.yaml` 加 `v3-live.yaml` 启动；沿用 Bake，不改生产入口。确认 API／Worker 均为本次固定 SHA 后，显式调用：

```sh
uv run --no-sync python -m ppap_corpus_contracts.live_poc \
  --api http://127.0.0.1:8080 --candidate-sha <full-candidate-sha> \
  --isolated-login --output /tmp/ppap-v3-live-new-run
```

已有 isolated-dev 授权可通过 `PPAP_LIVE_COOKIE` 注入，不在参数、日志或报告中保存。命令只接受独立栈，先检查候选、收费策略及完整 27 样例／8 组／220 预期。5 份原始 DOCX 经上传、解析、真实出处核对和发布后，逐组明确绑定并受理；不从检查器生成规则。每组仅 accept 一次，响应丢失只 GET 同一 Draft 的 Round，失败或超时不重新受理。输出目录必须不存在，避免误用同一命令重跑。

完整 canonical suite 含需要 OCR 回退的控制计划 PDF。使用本 Live overlay 的 in-process OCR 路径时，执行前须提供真实 `PADDLE_OCR_URL` 及该服务需要的授权；LLM 配置或离线 OCR cache 不能证明此能力可用。workflow 在任何业务写入前检查地址已配置，但配置存在不代表外部服务验收通过。

复用参考环境时，须分别核对服务级配置与仓库根配置；两者不是同一来源。迁入代码不会继承参考服务的 OCR 配置，独立栈须显式注入当前 Settings 所需的 Paddle／模型变量，不增加运行时跨仓配置回退。

每次写操作前保存安全回执，JSON／Markdown 保留逐条结果和失败阶段；只读到终态、冻结输入一致且 220 项逐一匹配才返回 `PROVEN`。缺项、重复项、ERR、状态不符或未完成均为 `NOT_PROVEN`，归因默认 `inconclusive`。不要仅凭退出码推断缺陷归属或自动重跑。可选证据位置不参与 PASS/NG 门槛。这里证明 Product API／Worker 能力，包含显式 SCL 审核；不改变 Native 客户流程中 SCL 仅为比对资料的政策，也不证明浏览器、飞书登录、PDF/ZIP、收费或生产放行。运行授权、预算和真实结果只记在 owning Issue，不新增运行台账或提交报告文件。

规则结果通过同一 Round 中冻结的 FileVersion → RuleVersion、coverage 的文件／规则索引及唯一 finding 关联。coverage 的 inline `rule_version_id` 是可选字段，不能把缺省当作缺项；若返回该字段，必须与冻结绑定一致。错误绑定和重复 finding 均拒绝，但不要求一个可选表示成为唯一证明。失败后的只读诊断报告须保留原始报告，并明确未重新受理或调用模型／转换服务，不能冒充新候选的一次成功验收。

Live 工作流失败时先保留本项目容器状态及 allowlist 字段的结构化服务日志，不输出原始模型／HTTP 日志。结束时仅停止本次 project；其 PostgreSQL／S3 命名卷保留新验收记录，不使用 `down -v`。完成必要读回并确认不再需要这些测试记录后，才对该明确 project 清理卷；不得清理共享开发栈或其他 project。

Repository contract：

```sh
uv sync --locked
git show --format= --check HEAD -- . ':!assets' ':!tests/corpus/fixtures'
uv run --no-sync python -m compileall -q packages services tests
uv run --no-sync ruff check packages services tests scripts
uv run --no-sync ruff format --check packages services tests scripts
uv run --no-sync mypy --no-incremental
node deploy/insforge/verify-config.mjs
docker compose -f deploy/isolated-dev/compose.yaml config --quiet >/dev/null
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example config --quiet >/dev/null
docker buildx bake --print >/dev/null
docker buildx bake --check
node scripts/verify-container-graph.mjs
deploy/tke/render-current.sh \
  | docker run --rm -i ghcr.io/yannh/kubeconform:v0.6.7 -strict -summary -kubernetes-version 1.28.0 -
uv run --no-sync pytest tests/repository
```

Distribution closure（这是 distribution correctness proof；root `uv sync` 只保留为快速 workspace smoke）：

```sh
python3 scripts/validate_distribution.py --discover
python3 scripts/validate_distribution.py --member ppap-core
python3 scripts/validate_distribution.py --prove-missing-dependency
```

service distribution 的 owner tests 与普通 service module job 一样，需要先启动 isolated-dev PostgreSQL fixture。

Python module groups：

```sh
# One pytest process per physical owner. CI discovers every packages/*/tests and services/*/tests.
uv run --no-sync pytest packages/ppap_application/tests
uv run --no-sync pytest packages/ppap_core/tests
uv run --no-sync pytest packages/ppap_corpus_contracts/tests
uv run --no-sync pytest packages/ppap_deep/tests
uv run --no-sync pytest packages/ppap_excel_audit_engine/tests
uv run --no-sync pytest packages/ppap_ocr_adapter/tests
uv run --no-sync pytest packages/ppap_product_store/tests

export COMPOSE_PROJECT_NAME=ppap-api
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest services/api/tests
export COMPOSE_PROJECT_NAME=ppap-worker
uv run --no-sync pytest services/worker/tests
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

Cross-owner product contracts：

```sh
export COMPOSE_PROJECT_NAME=ppap-product-integration
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest tests/integration/product
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

Corpus：

```sh
uv run --no-sync pytest tests/corpus/v2_assets --ftype excel -q
uv run --no-sync pytest tests/corpus/v2_assets --ftype pdf -q
uv run --no-sync pytest tests/corpus/v2_assets --ftype scanned -q
uv run --no-sync pytest tests/corpus/pairs --cross-format -q
uv run --no-sync pytest tests/corpus/v3_poc -q

export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker buildx bake worker
docker build \
  --build-arg PPAP_WORKER_IMAGE="ppap-worker:${PPAP_BUILD_SHA}" \
  --build-context psw-tests=tests \
  --build-context psw-assets="assets/V2.0测试/模拟测试文件V2-01/16-零件提交保证书" \
  -f tests/corpus/Dockerfile.psw \
  -t "ppap-psw-test:${PPAP_BUILD_SHA}" \
  .
docker run --rm "ppap-psw-test:${PPAP_BUILD_SHA}" \
  pytest tests/corpus/psw -q
```

Schema：

```sh
export COMPOSE_PROJECT_NAME=ppap-schema
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest tests/schema
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

Product integration 使用 #100 的单次完整 bake 与隔离 Compose 生命周期：

```sh
uv sync --locked --group e2e
uv run --no-sync python -m playwright install chromium
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"

export COMPOSE_PROJECT_NAME=ppap-product-integration
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest tests/integration/product
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans

docker buildx bake

export COMPOSE_PROJECT_NAME=ppap-prod
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example up -d --wait
uv run --no-sync pytest tests/integration/runtime
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example down -v --remove-orphans

export COMPOSE_PROJECT_NAME=ppap-e2e
docker compose -f deploy/isolated-dev/compose.yaml up -d --wait --no-build
uv run --no-sync pytest tests/e2e
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

PSW 的 `.doc` 用例需要 LibreOffice 和 Linux 上可执行的 `ppap-local-converter-sandbox`。CI 与推荐本地路径是先 `docker buildx bake worker`，再构建 `tests/corpus/Dockerfile.psw` 派生测试镜像并在其中跑 pytest（worker 已含 LibreOffice 与 sandbox；测试镜像对同一 `uv.lock` 执行 `uv sync --frozen --no-editable`，装上默认 dev dependency group；测试文件经 build context COPY，不 bind-mount 仓库）。`.doc` 转换是默认行为，没有开关变量；转换器或沙箱缺失时该项记 ERR，不跳过格式。需要 Paddle 的 PDF 转换优先使用 `tests/corpus/fixtures/ocr_cache`；未命中时再读 `PADDLE_OCR_URL`。这仍是 required corpus regression，不是 live。人工 expected 以 corpus 内 CSV/XLSX 为准，不要改它来迎合实现。

#7 的代表性浏览器 E2E 从当前 Web 上传待审与参考文件，经公开 Product API 显式绑定 fixture 规则并受理，由真实数据库、对象存储和 Worker 完成执行。浏览器核对两轮冻结原件的下载字节、人工复核持久化，以及返回后的条款、文件、筛选恢复和保存后继续。规则绑定、受理和显式新轮准备由 API 完成，不作为 V3.1 自动匹配与确认界面已完成的证据；presence fixture 也不证明真实 PPAP 审核质量。

```sh
uv sync --locked --group e2e
uv run --no-sync python -m playwright install chromium
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
export COMPOSE_PROJECT_NAME=ppap-e2e
docker buildx bake
docker compose -f deploy/isolated-dev/compose.yaml up -d --wait --no-build
uv run --no-sync pytest tests/e2e
docker compose -f deploy/isolated-dev/compose.yaml down -v
```

Live Deep 会调用付费模型。先在仓库根目录的 `.env` 配置 `LLM_API_KEY`、HTTPS `LLM_BASE_URL` 和 `LLM_MODEL`。以下命令构建专用 amd64 Deep 镜像，Controller 留在容器内，Worker/API 只通过 UDS 访问；无需 gVisor、Docker socket 或宿主机 Grok 配置：

```sh
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker buildx bake worker --set worker.platform=linux/amd64 --load
docker buildx build --platform linux/amd64 --load \
  --build-arg PPAP_APP_IMAGE="ppap-worker:$PPAP_BUILD_SHA" \
  -f deploy/deep/Dockerfile -t "ppap-deep:$PPAP_BUILD_SHA" .
mkdir -p /tmp/ppap-deep
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml up --no-build -d --wait
PPAP_DEEP_LIVE=1 uv run --no-sync pytest tests/live/test_deep_isolated.py
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml down -v --remove-orphans
```

Overlay 显式设置 `PPAP_DEEP_EXECUTOR_REVISION=ppap-deep-v2`、`PPAP_DEEP_RUNTIME=container-only`、`PPAP_DEEP_SANDBOX_PROFILE=off`。旧内核的 `off` 不提供强文件系统或出口网络隔离；只允许此容器级模式，不要声称是 gVisor。此 live 协议 smoke 上传玩具但有效的 XLSX 与已发布规则，要求带实际单元格证据的语义结果和已记录的模型用量；固定 checker、脚本 guest 或仅有 PASS 状态都不算审核证明。客户 A1–A12 仍须另用真实样例验收。默认 isolated-dev 仍不发布 Silo 端口，remote-dev 的现有配置不受此 overlay 影响。

模型 grant 的默认额度是 4,000,000 输入 token、100,000 输出 token、128 次请求和 40 分钟。输入额度按提供方返回的已确认累计用量停止后续请求，不是严格的费用硬上限：加密推理历史无法在请求前准确计数，故最多一笔受 8 MiB 请求体上限约束的在途请求可能令输入用量超额。输出请求按剩余额度限制；用量缺失即关闭 grant，不把请求字节误记为 token。单测验证这些边界；live smoke 仍需记录真实提供方用量。本轮开发已获授权可依据实测提高这些上限，但每次调整仍须保留有限额度并说明原因和实际用量，不以无限重跑替代修复。CETUS 完整规则实测触及原 1,600,000 输入额度后提高至当前值；后续联合审核在原 20 分钟截止时仍未执行完毕，因此时长提高至 40 分钟。这不是新的完成率保证。

Deep gateway 将业务完成与 provider 用量回执分开：`response.completed`、`response.incomplete` 和 `response.failed` 中有效的输入／输出 token 都只记一次，原事件不改写；缺失、类型错误或负数回执仍保留未知并关闭 grant。同一已提交请求不得自动重放。`test_deep_gateway_usage.py` 经真实 loopback HTTP／UDS 入口验证该边界，只替换外部 HTTPS 响应，不证明真实模型质量、外部扣费或跨进程恢复。

上下文窗口和自动压缩沿用 grok-build 对所选模型的默认配置；PPAP 不写 `context_window` 或自定义压缩阈值，也不为了验收强制压缩。单测只证明没有覆盖配置及事件计数正确；真实压缩仍以 `compact_boundary` 事件及压缩后规则、证据完整性为准。读取单次提示用量时，将互斥的未缓存、缓存读取、缓存写入三项相加，不使用累计输入额度判断上下文长度。

组织内测试资产和可访问环境按 [AGENTS.md](../AGENTS.md) 的默认规则直接使用。

`tests/integration/product/test_rules.py` 通过真实 Excel/Word 提取与模拟外部 LLM 验证规则解析，再通过 Product API / Worker 验证受理、预览、发布、版本不变与来源授权。`packages/ppap_excel_audit_engine/tests/test_rule_ai_client.py` 验证每调用超时、零自动重试及错误日志脱敏。真实 PostgreSQL 契约在 `tests/schema/`：CI 用 `pytest tests/schema` 收集全部 `test_*.py`，共用 isolated-dev PostgreSQL fixture。其中包括 empty/previous schema 升级、规则解析的并发受理/租约/迟到完成/来源授权、审核绑定与用量分配。新增同类测试放到该目录即可进入 schema CI。`tests/e2e/test_web_rules.py` 读取 Web `runtime-config.json` 中的 API 地址，再通过隔离登录接口建立测试会话；不假定静态 Web 提供 `/api` 代理。从当前审核要求入口使用结构化规则文件走真实上传、提取、人工编辑和保存；再用全新浏览器会话重访只读版本，并下载核对原件。它不证明真实飞书登录。`tests/e2e/test_rule_interactions.py` 在浏览器 API 边界控制迟到响应，验证整页操作互斥、刷新只读、显式重跑，以及 `deep_offered` 对审核模式控件的门控。这些测试不调用外部模型服务。

原生上传预检查的读取失败用例也在 `test_rule_interactions.py`：一份文件读取失败不取消另一份可审文件；重新读取只查询同一个已知检查工作，不新建检查、审核任务或受理。

规则提取回执的工作编号、文件、固定版本与资料类型必须符合原请求，错配不能替换草稿；核对只读原请求。JSON 中的类型与所选类型不同会明确失败，用户可在同一原件上更正类型后显式创建新提取请求。上述浏览器测试同时保留无规则原件的旧轮次只读回访；新提取门禁不能应用到历史条款投影。`test_rules.py` 用真实 API／PostgreSQL／Worker 验证类型冲突与更正、原件字节不变及未发布规则；JSON 路径不调用模型，对象存储仍为受控边界。

`tests/e2e/test_web_audit.py::WebAuditLoopTests::test_audit_files_path_loads_history_after_login` 从现有 `/audit-files` 深链读取服务器轮次，验证当前工作台搜索、冻结轮次入口和列表只读回访；隔离 fixture 的 presence 规则不证明真实 PPAP 审核质量。工作台初次读取期间，用户显式选择的队列优先于数据返回后计算的默认队列；`test_explicit_all_records_selection_survives_initial_history_read` 控制响应顺序验证此边界。

`tests/e2e/test_report_sharing.py` 验证固定 PDF/ZIP、中文字符映射、报告切换和撤权后拒绝访问，不读取私有任务或原件。静态构建将已安装 PDF.js 的 `cmaps`、`standard_fonts`、`wasm` 资源及许可证放入 `dist/pdfjs`；开发环境继续使用本机依赖。Nginx 必须以 JavaScript MIME 提供 `.mjs` worker；`tests/repository/test_web_server.py` 通过实际 Nginx 请求检查该响应和不存在文件的 404。

`tests/e2e/test_report_packages.py` 使用原生档案组件与受控 HTTP，验证原 ZIP 请求的刷新恢复、固定下载字节、失败后只读恢复、错误回执拒绝及切页后的迟到响应；不据此证明真实服务或飞书送达。跨任务包的实际 PostgreSQL／Worker 回归位于 `tests/schema/test_audit_bindings.py`，覆盖同名 PDF、固定字节、反序请求复用、全部来源授权、原集合重试与旧 attempt 拒绝。固定模板与联系链接由 `tests/e2e/test_template_links_preview.py` 和 `test_product_access.py` 验证新窗口打开及审核草稿保留，不把链接打开当作反馈送达。

其中的未知提取恢复用例从当前审核要求上传入口进入：刷新和核对只读原请求，记录 HTTP 方法以拒绝恢复期间的写操作；重新提取需确认可能的模型费用并创建新请求。原请求的迟到读取不能替换新请求；挂起的提交响应晚于人工录入返回时不能覆盖人工内容。`RuleExtractionRecovery.tla` 是一份冻结来源、两个请求及延迟响应的有限 Safety 检查；正向配置检查未知不冒充运行中、只读不重提、当前请求及人工录入边界，四个故障配置保留对应反例。它不证明 SQL 持久化、跨标签页恢复、授权、模型质量、真实收费或最终完成。

批量上传恢复从当前“上传资料”和草稿内“添加审核资料”进入。`test_rule_interactions.py` 控制逐文件回执、保存拒绝和迟到读取，要求已收到文件保留、未发送与未知分开、查询不新建传输，以及离开后的结果不写入另一草稿。`test_upload_recovery.py` 通过真实 Product API/数据库上传三份 XLSX，分别中断第三份已完成传输的回执、或传输前已完成草稿保存的回执；清除本机记录后读回同一草稿，查询或显式继续原请求完成关联。继续操作固定原请求 ID 和文件摘要，复用现有幂等创建，不把“未查到”当作新建身份的许可。测试不启动审核或扣费；对象存储的实际持久性另由运行环境验证。`UploadBatchRecovery.tla` 是两个页面 owner、三份文件的有限 Safety 检查，保留丢失成功文件、新建未知请求及迟到提交的故障配置；同身份创建/写入的幂等性由 API/存储边界验证，模型不证明完整系统 refinement、跨标签页并发、SQL/对象存储持久性或最终完成。

## Web OpenAPI contract

报告分享的物理连接失效回归仍由 `tests/schema/test_audit_bindings.py` 拥有。共用发送生命周期沿用 `ReportShareDispatch.tla`：两个接收人、各两次尝试的有限 Safety 检查；无公平性、状态约束、对称缩减或覆盖，静止终态不按死锁处理，不证明权限、文件内容、无限重试或最终送达。

API owner tests validate that every major Web-consumed endpoint has an explicit successful JSON response schema and that JSON write endpoints have an explicit request schema. Repository CI then installs the frozen Web lockfile inside `apps/web`, runs `pnpm api-types` there, and fails if `apps/web/src/generated/openapi.ts` differs from the committed artifact. Web CI typechecks/tests/lints/builds the generated consumers.

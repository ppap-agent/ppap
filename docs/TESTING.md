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
- **integration**：数据库、对象存储、Worker、授权和必要外部接口。Deep 的受理、版本、fencing 与传输边界由 `tests/integration/product/test_deep_mode.py` 验证；语义 guest 与容器权限/模型 grant 的确定性逻辑由 `packages/ppap_deep/tests/test_deep_runtime.py`、`packages/ppap_deep/tests/test_deep_semantic.py`、`packages/ppap_deep/tests/test_deep_isolation.py` 验证。脚本化 runtime 只证明协议，不证明真实审核。同文件的局部计划失败用例使用实际 semantic guest，经 Product API／Worker／真实 PostgreSQL 与 UDS，核对当前任务和冻结历史的逐条结果、原件及独立文件的报告入队；模型、S3 边界受控，薄进程内适配不证明容器隔离、模型质量、飞书档案身份或 PDF 生成完成。`tests/live/test_deep_isolated.py` 使用独立 `ppap-deep` 容器、真实 Grok 和模型，但仅是玩具 XLSX 的真实模型协议 smoke，不是客户 A1–A12 样例验收；它不进入默认 merge contract。

`packages/ppap_deep/tests/test_deep_client.py` 使用真实 UDS 验证未送达请求的显式恢复，以及分片响应、发送背压中的总时限与取消。外部 peer 受控；不调用模型，也不证明 provider 已取消或执行域已清理。总时限从调用开始计算，帧内读写不能重置预算；已发送请求超时不等于未执行，客户端不自动重发。

`packages/ppap_deep/tests/test_deep_controller.py` 验证公开 Controller 的读取取消及真实 UDS 入帧边界：当前对象读取返回后，取消会停止后续读取与 Runtime 派发；不承诺撤销已在途读取。服务端 `request_timeout` 从接收连接开始约束整份入站帧，片段不续期，同时保留 socket 的空闲超时；它不是 Deep 审核的执行时限。超时后正常请求仍可派发；已接收但晚于 close 才登记的连接不能启动授权或读取。测试控制对象存储、外部 peer 及 OS accept 返回时序，使用实际未配置 Runtime 检查派发边界，不提供模型质量、PG 或强隔离证明。现有 `DeepExecutionQueue.tla` 不覆盖这些读取阶段，本批没有新增 TLC、完整 shutdown 或生命周期证明。

Controller 原件读取与 guest 暂存复用 `ppap_deep.limits` 中的文件数和总字节上限。超出文件数时不读对象；逐对象读取使用剩余字节预算，超限停止后续读取及 Runtime 派发，返回既有 `deep_budget_exhausted`。S3 有界读取不依赖声明大小正确，短读会继续，响应流在成功或失败后关闭。对应 Controller／Core storage 测试仅控制外部存储与小额测试预算，不分配大文件。该提前检查不代表资料已能装入执行环境：暂存还包括规则、缓存及清单，原暂存总量检查继续保留；不是新增用户上传配额，也不证明进程树内存或在途读取取消。

`test_deep_partial_failure.py` 使用真实 XLSX，经 Controller、`SemanticRuntime`、进程内真实 guest 和输出校验，验证一套计划持续失败不会丢弃独立文件；共享失败规则的所有条款保留为 ERR，冻结目标不删除，全部失败也不能变成空 PASS。模型返回的 RulePlan 结构不符同样进入原有的一次有界修复，仍无效则只标记对应规则失败；原 deadline、用量与独立文件保留，不把预算、超时、模型协议或未分类异常变成局部成功。结构校验反馈只包含固定说明，不保存 Pydantic 错误中的模型原文。仅模型响应和对象存储受控，薄 runtime 不证明容器隔离、真实模型质量或 Worker／数据库发布。主机拒绝错身份、缺项、重叠分区及失败条款的伪造业务结果；有效计划可缓存，失败计划不缓存，所有模型尝试保留用量。

`tests/integration/runtime/test_runtime.py` 在同 SHA 的交付 Worker 镜像中运行 `deep_container_cases.py`，验证公开 `ContainerRuntime.run` 的排队超时、取消和执行位释放。真实 Linux、构造器、锁及文件准备/清理不替换；外部 guest 进程受控，版本探针使用测试程序，不调用 Grok 或模型，不证明语义质量或强隔离。`DeepExecutionQueue.tla` 只检查一个占用者与一个等待者：即使占用者不结束，到期/取消仍能响应；正向配置检查允许时钟和等待线程持续获得调度时的终结性质，`-unbounded` 配置保留响应依赖占用者释放的反例。它不证明毫秒级时限、运行中取消、整个进程树清理或完整系统 refinement。

同一 native 入口还通过真实本地 gateway（Unix grant 控制／loopback HTTP）验证执行包读回、同 execution 并发合并、已消费后失败不重放、未知 provider 完成状态以及显式新 submission。仅 HTTPS provider 与 guest 进程受控；原生锁、journal、attempt、gateway 记账及目录不替换。runtime 重建与重复 recover 使用同一保留状态、在上一 guest 已终结后执行，不等于进程中途崩溃、丢卷或跨节点恢复。journal 输出仍须经过业务验证，不作为实际审核质量或报告完成证明。`DeepExecutionReplay.tla` 是两个调用共享一个冻结 execution 的有限 Safety 检查；race／failure／result 配置分别保留不加锁、重放失败 attempt、忽略既存输出的反例，不证明完整 refinement、最终完成或外部平台幂等。

`test_deep_gateway_cancellation.py` 经公开 grant UDS／loopback HTTP 撤销一个执行的授权：provider 在响应头、正文或终态回执之后保持静默，撤权主动中断其已连接 socket，handler 清理后下一独立执行可继续。测试只替换外部 HTTPS 适配，实际 socket 读取／关闭、共享锁和记账运行；分别核对撤权前的未知消费或有效回执，以及下一执行的独立用量，不重发原请求。关闭本地连接不证明 provider 已取消或未收费。`DeepGatewayRevocation.tla` 是一个撤权执行与一个等待执行的有限 focused obligation；不假设 provider 必定响应，只对本地中断、清理及下一执行的调度施加弱公平，`-dependent` 保留取消错误地依赖新数据的反例。它不证明完整 refinement、毫秒时限、DNS／TCP／TLS 建连、重发 grant、过期回收、崩溃恢复或内核隔离。

缓存用例在同一入口执行真实 XLSX → `SemanticRuntime` → `ContainerRuntime` → guest 解析、计划／结果验证和产品转换。进程替身在测试进程内调用真实 guest，模型 StageRunner／HTTPS 和对象存储为外部边界替身，不证明实际 Grok、S3 耐久性或 guest 权限隔离。两份目标共用规则，新 submission 复用有效 snapshot／plan 但重新审核；缓存中的无害解析注记区分实际复用与重新解析，错误摘要／计划 revision 分别重新解析／编译。原 submission 再读不执行新审核，gateway 记录的受控请求数与用量同步核对。这是执行与缓存合同，不是客户判定质量或生产费用证据；现有单 execution 的 Replay 模型不覆盖跨 submission 缓存。
- **E2E**：少量关键客户路径和跨栈边界，覆盖当前上传、历史轮次、原件与人工复核；各入口区分受控交互和真实服务证据，不据此宣称全部客户流程完成。飞书 Drive 导入与文档关联文件的确定性回归在 `tests/integration/product/test_feishu_drive.py` 与 `services/api/tests/test_feishu_doc_links.py`（mocked Open API）。docs-addon 宿主登录到普通 round 的确定性回归在 `tests/integration/product/test_feishu_docs_addon.py`（mocked host code + Open API）。客户路径 `/addon`、`/audit-files` 与 `/usage` 由 nginx 自己检查：`nginx -t` 加上容器内的路由请求，在 `tests/repository/test_web_server.py`。Web 只调 Product API 的仓库边界、helper、路由、reducer 与 public-config 都由 `apps/web` 的 `pnpm test` 拥有；跨栈黑盒浏览器行为由 Python Playwright（`tests.e2e`）拥有。InsForge overlay 的 **static config contract** 由 `node deploy/insforge/verify-config.mjs` 拥有：Compose 语法与插值交给 `docker compose config`，再检查 Compose 无法知道的少量 PPAP invariant。**live native lifecycle** 是 `bash deploy/insforge/smoke-local.sh`（`@insforge/cli local start/status/db query/stop`），只在 InsForge 相关 path 变化、手动 `workflow_dispatch` 或每周 schedule 上运行。

规则排列组合留在低层，不复制到 E2E。页面与历史轮次的读取经已授权的 Product API 验证；Worker 只用于驱动队列，不以 Worker 角色读取仅供 API 的页面元数据。

`tests/e2e/test_ui_a11y.py` 从当前工作台与审核要求入口验证键盘导航、单一主标题、窄屏，以及原件侧栏和草稿确认弹窗的焦点约束与取消返回；API fixtures 只用于受控 UI 验证，不证明真实登录、发布或数据持久化。

规则导入的出处来自固定文件版本的原文字段，不从“待核对”等展示文案推断。`test_rule_sources.py` 与 Product `test_rules.py` 验证真实位置、摘录和发布校验；`test_rule_interactions.py` 验证旧草稿仅补充可唯一匹配的出处、不重新提取或发布，以及原文选择、核对计数和保存门槛一致。`test_web_rules.py` 通过真实 API、PostgreSQL、对象存储与 Worker 验证 JSON 及三份 POC 原始 DOCX 的上传、核对、保存和全新会话重开，检查用户确认的 9／7／11 条原文与顺序、核对状态、来源版本及下载字节。结构清晰的 DOCX 规则表直接提取原文，不要求模型调用；`test_control_plan_rule_assets.py` 保护控制计划原始资产及本地 11 条解析。上述隔离栈测试不证明真实飞书登录或全部 POC 在线审核质量。

`tests/integration/product/test_native_docx_worker.py` 覆盖设备清单 V2 和 PSW V3 的原生 Word 文件：公开上传、JSON 规则解析／发布、显式版本绑定、重复确认、实际 Worker 角色执行及 PostgreSQL 结果读回。Word 解析、固定审核器、冻结版本和结果投影不替换；只控制对象存储与外部视觉模型响应。完整／缺少图片的 PSW 分别核对逐条结果，重复读取不执行新审核，原件字节保持不变。这些用例不证明原始规则 DOCX 提取、真实视觉识别、飞书、浏览器或 PDF 生成；数据库验证在隔离 CI 中执行，不指向共享开发库。

同一冻结原件的出处目录迟到且仍支持当前引用时，不清除版本确认；出处有效性变化或用户编辑仍使确认失效。受控测试在确认后显式交付目录响应，不用延时或加长超时代替此边界。

报告分享目标目录的只读请求共用 30 秒响应预算，覆盖本人身份、应用／租户 token、搜索与逐项目标核验。Core 分享与 API `test_share_directory.py` 通过受控 HTTP／时钟检查调用前后截止、剩余阶段超时及停止后续请求；晚到或未核验目标仍 unknown，已核验结果和分页顺序保留，身份／目录阶段超时为可恢复的 503。仅目录读取使用该预算，发送／回执／失败目标重试维持原政策。HTTPX 阶段超时不是底层传输硬实时取消或新产品 SLA；这些无数据库测试不证明真实飞书延迟、资格或送达。 Web 单元及 `test_report_sharing.py` 的原生 HTTP 浏览器回归检查 45 秒客户端截止、保留选择、显式恢复原查询及分页、忽略晚到响应，并确认全程不发送消息；不模拟内部分享状态，也不以受控回执计作真实送达。

失败轮次的新轮准备由 `test_rule_interactions.py` 覆盖正常、丢失／无效回执、错身份、刷新恢复与离开后的晚到响应，以及多类型／同类型不同规则在编辑和重新读取后的绑定保留；迟到响应先等待响应完成与渲染边界，再检查未发生导航。`test_audit_flow.py` 使用独立 PostgreSQL 验证草稿初始输入、并发同身份重放、撤权、事务回滚及不新增工作／用量；`test_audit_tasks.py` 验证 V3 终态不能绕入旧式重审入口。`RoundPreparation` 是单次准备、两个请求身份、三个导航代次的有限安全模型；数据库去重与授权是外部假设，不证明完整审核、跨窗口原子性或最终网络恢复。准备步骤不改变 `GreenfieldCheckStart` 中的受理计数。

`test_audit_flow.py` 的 Standard 结果写回故障用例通过公开受理、真实 Worker／PostgreSQL 和公开 Round 读回验证：首次 findings 写入事务回滚不能被误归为审核器异常，再提交伪 ERR。完成事务在审核器异常捕获之外；保存失败向调用者传播，原未完成 attempt 不被伪造终结，不自动创建新轮次或消费。此用例不证明持久模型结果缓存、provider 去重或完整技术恢复。

原生关联重审的补件选择由 `test_reaudit_selection.py` 经界面和受控 HTTP 验证：改选／取消替换、取消勾选／移除补件时，只恢复明确记录为此前已选的自动排除原件；用户主动取消或移除的文件不恢复。旧草稿仅有自动移除原因、未保留此前选择时，解除替换后显示为未选，不猜测丢失的意图。“全不选”包含新自动排除项，刷新读回后仍可解除替换或重选补件。补件保留原件明确选择的规则版本或“重新匹配当前规则”意图，不用首个候选覆盖空版本选择；受控回执也以本次输入为准，不回填准备页旧偏好。`test_audit_tasks.py` 的数据库回归验证未选／已移除补件的绑定保存在当前 revision 的既有关联 job 回执中；不为这些文件创建识别 job，不依赖回执所在文件仍 selected，也不读取旧 revision 的孤立 job。不新增数据库迁移、历史改写或浏览器账本。`ReauditReplacementSelection` 是两个冻结原件与一个补件、已知选择的有限 Safety 检查，覆盖重绑定、单选、移除和批量选择；三个故障配置保留不释放旧原件、单选不协调和批量取消遗漏的反例。旧格式的保守处理另由界面回归验证；模型不证明真实审核、持久化、收费或整系统 refinement。

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

Standard 的 `run_round_from_bytes` 用独立、执行后即清理的跟踪范围收集 SDK 可见用量，返回到已有 Worker `usage`／attempt 诊断边界，不新增客户用量 API。已报告的输入／输出 token 不因模型输出无效而丢失；任一 SDK 调用缺少有效输入／输出回执时，`usage_complete=false`、总量为 `null`，已知调用明细保留。`usage_complete` 只表示可见 SDK 调用的输入／输出回执完整性；可选 reasoning 拆分缺失／无效时显示 `null`，不丢弃已知输入／输出，也不重复加入总量。正常返回或异常退出均恢复调用前的范围，下一次执行不继承旧统计。Core Tracker、真实 Word／审核器和外部 HTTP／SDK 边界测试保护此行为；它不改变 PASS／NG、既有重试或每份 500 点业务账，也不证明 SDK 内部全部 HTTP 尝试、实际扣费订单或当前候选的 PG 持久化已验收。

`tests/integration/product/test_standard_usage_persistence.py` 在隔离 Product 数据库中补充公开上传、JSON 规则解析／发布、显式冻结受理、实际 Standard Worker／Word 审核器与 PostgreSQL attempt 诊断读回。对象存储和 SDK 外部 HTTP 响应受控，分别检查完整、混合及缺失回执；已知明细与未知总量保持，实际 PASS／NG、冻结版本、原件和重复只读不执行保持。数据库场景须由隔离 CI 实际执行，收集不算通过；不新增开发者回执 API，不证明真实视觉质量、SDK 隐藏重试、结算订单或旧自动技术重排。

### 显式 V3 POC Live

`V3 POC live acceptance` 只接受 `workflow_dispatch`，不进入默认 `CI Gate`。它用当前 SHA 的 API／Worker 镜像和独立 PostgreSQL／S3，凭 Secrets 调用真实模型／转换服务；收费和飞书通知关闭。执行前核对两个镜像的 revision，不能让现网旧 Worker 消费候选 API 创建的验收任务。

独立栈可通过 `deploy/isolated-dev/compose.yaml` 加 `v3-live.yaml` 启动；沿用 Bake，不改生产入口。确认 API／Worker 均为本次固定 SHA 后，显式调用：

```sh
uv run --no-sync python -m ppap_corpus_contracts.live_poc \
  --api http://127.0.0.1:8080 --candidate-sha <full-candidate-sha> \
  --isolated-login --output /tmp/ppap-v3-live-new-run
```

`--analysis-mode standard|deep` 显式选择执行模式，缺省仍为 Standard；现有远端 Live workflow 未因此改为 Deep。若另获授权使用具备当前候选 Deep sidecar 的独立栈，可在同一命令加入 `--analysis-mode deep`，并使用新的输出目录。runner 将所选模式写入报告，核对受理与完成读回的冻结模式、executor 和 revision；Deep 要求实际策略摘要，不能静默回退 Standard。这里仍只比较同一 canonical 27 样例／220 条人工预期，不代表 #166 的独立保留集、完整质量、成本、隔离或恢复验收已完成，也不构成执行 Live 的授权。

可重复传入 `--bundle 'Aries Spoiler' --bundle 'POC测试样本'`，精确选择 `build_bundle_map` 中已有的完整文件组；不按文件或规则拆组。省略参数仍执行全部 8 组，显式选择全部 8 组也记为 `scope=full`；其他选择记为 `scope=subset`。报告的 `selected_bundles` 与 `denominators.full/selected` 明确列出总计及所选的组数、文件数、规则数。子集全部对齐只返回 `SUBSET_PROVEN` 和成功退出码，不返回完整 `PROVEN`，不代表完整 corpus 或产品验收。无效、空或重复选择在登录及任何 HTTP 请求之前失败。组名可用以下纯离线命令查看，不会启动审核：

```sh
uv run --no-sync python -c 'from ppap_corpus_contracts.poc_oracle import build_bundle_map, load_poc_cases; print("\n".join(sorted(build_bundle_map(load_poc_cases()))))'
```

已有 isolated-dev 授权可通过 `PPAP_LIVE_COOKIE` 注入，不在参数、日志或报告中保存。命令只接受独立栈，先核对完整 27 样例／8 组／220 预期及选择，再检查候选、收费策略。完整运行发布 5 份原始 DOCX；子集只上传和发布所选组需要的规则 DOCX，仍核对原文、出处与发布版本，再逐组明确绑定并受理，不从检查器生成规则。每组仅 accept 一次，响应丢失只 GET 同一 Draft 的 Round，失败或超时不重新受理。输出目录必须不存在，避免误用同一命令重跑；小批次选择也不构成额外 Live 或付费调用授权。

完整 canonical suite 含需要 OCR 回退的控制计划 PDF。使用本 Live overlay 的 in-process OCR 路径时，执行前须提供真实 `PADDLE_OCR_URL` 及该服务需要的授权；LLM 配置或离线 OCR cache 不能证明此能力可用。workflow 在任何业务写入前检查地址已配置，但配置存在不代表外部服务验收通过。

复用参考环境时，须分别核对服务级配置与仓库根配置；两者不是同一来源。迁入代码不会继承参考服务的 OCR 配置，独立栈须显式注入当前 Settings 所需的 Paddle／模型变量，不增加运行时跨仓配置回退。

每次写操作前保存安全回执，JSON／Markdown 保留逐条结果和失败阶段；只读到终态、冻结输入一致且所选规则全部逐一匹配才通过所声明范围，只有完整 220 项对齐才返回 `PROVEN`。子集的未完成规则仍保留在所选分母中，未选组不冒充已验证。缺项、重复项、ERR、状态不符或未完成均为 `NOT_PROVEN`，归因默认 `inconclusive`。不要仅凭退出码推断缺陷归属或自动重跑。可选证据位置不参与 PASS/NG 门槛。这里证明 Product API／Worker 能力，包含显式 SCL 审核；不改变 Native 客户流程中 SCL 仅为比对资料的政策，也不证明浏览器、飞书登录、PDF/ZIP、收费或生产放行。运行授权、预算和真实结果只记在 owning Issue，不新增运行台账或提交报告文件。

规则结果通过同一 Round 中冻结的 FileVersion → RuleVersion、coverage 的文件／规则索引及唯一 finding 关联。coverage 的 inline `rule_version_id` 是可选字段，不能把缺省当作缺项；若返回该字段，必须与冻结绑定一致。错误绑定和重复 finding 均拒绝，但不要求一个可选表示成为唯一证明。失败后的只读诊断报告须保留原始报告，并明确未重新受理或调用模型／转换服务，不能冒充新候选的一次成功验收。

完成读回还逐项核对冻结条款与 coverage／finding 集合，不接受额外条款或孤立 finding。Standard 使用 `mapped`、Deep 使用 `executed` 的实际公开 coverage；Deep 局部 `execution_error`／ERR 记为执行失败而非缺项，仍保留在 220 条分母中。可选证据缺失不参与该比较门槛。

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

`test_workbench_task_state.py` 使用原生 Web 与公开 HTTP 回包，分别验证正常 NG 和局部执行失败：列表、详情、返回及刷新保持相同分类；先暂缓返回后的列表响应，避免后续服务端摘要掩盖详情投影的差异。桌面及窄屏都保留用户显式选择的队列，首次入口仍默认全部；同页导航后的前进与后退也恢复各历史项的队列。队列仅是当前浏览器历史项的界面状态，不是业务记录；测试拒绝所有写请求，不证明真实审核质量或数据库持久化。历史报告测试不再承担这项任务分类回归。

`tests/e2e/test_report_sharing.py` 验证固定 PDF/ZIP、中文字符映射、报告切换和撤权后拒绝访问，不读取私有任务或原件。读取停滞场景在 45 秒客户端截止后只允许显式重读同一冻结产物；旧响应不能替换当前 PDF 或追加 ZIP 下载，不重新生成或重审。适配器测试另覆盖 fetch／响应体停滞及忽略 Abort 的外部边界；客户端结束不证明服务端工作已取消，也不是既有报告生成／恢复模型的完整 refinement 证明。静态构建将已安装 PDF.js 的 `cmaps`、`standard_fonts`、`wasm` 资源及许可证放入 `dist/pdfjs`；开发环境继续使用本机依赖。Nginx 必须以 JavaScript MIME 提供 `.mjs` worker；`tests/repository/test_web_server.py` 通过实际 Nginx 请求检查该响应和不存在文件的 404。

`tests/e2e/test_report_packages.py` 使用原生档案组件与受控 HTTP，验证原 ZIP 请求的刷新恢复、固定下载字节、失败后只读恢复、错误回执拒绝及切页后的迟到响应；不据此证明真实服务或飞书送达。跨任务包的实际 PostgreSQL／Worker 回归位于 `tests/schema/test_audit_bindings.py`，覆盖同名 PDF、固定字节、反序请求复用、全部来源授权、原集合重试与旧 attempt 拒绝。固定模板与联系链接由 `tests/e2e/test_template_links_preview.py` 和 `test_product_access.py` 验证新窗口打开及审核草稿保留，不把链接打开当作反馈送达。

`test_audit_preview.py` 的报告阅读回归核对单一选择表格、点击切换预览与勾选批量范围分离、PDF 工具栏首屏位置及窄屏操作。普通就绪状态不重复显示；生成时间、时区、规则版本和冻结引用收纳到“报告信息”，测试固定浏览器时区并保留原始 `datetime`。ZIP 固定集合在“打包记录”中保留完整文件名、准备状态和失败原因；下载所选 ZIP 必须与当前选择相同，恢复测试仍比较完整冻结引用及顺序，同名报告下载核对包内清单。`test_task_navigation_preview.py` 验证产品导航与任务切换分离、文件名识别线索，以及报告路径导航保留结果深链。受控交互不证明真实模型、服务持久性或飞书送达。

同一预览入口控制已有任务／报告的读取，核对加载中保留页面语境、不显示上传步骤或内部 UUID、不引导返回新建；失败重读、重新登录与迟到响应仍使用原地址和既有恢复边界。支持弹窗继续保留原服务请求和任务标识。骨架仅表示加载布局，不代表已读取的文件数量、审核进度或结果。

读取占位使用 `ReadSkeleton` 的列表、表格、详情、文档或行内布局；保留可访问状态名并支持减少动画。已有列表刷新时保留内容；上传、提交、审核执行及未知回执不作为普通读取隐藏。`test_journey_read_loading.py` 在受控 HTTP 边界检查登录首读、宽窄屏列表首读、刷新保留及失败重读，并拒绝写请求。`test_report_sharing.py` 与 `test_audit_preview.py` 检查报告追溯标识默认折叠、展开后保留原值，以及分享操作在宽窄屏不溢出；这些检查不证明外部送达。

`test_page_header_preview.py` 核对当前页面的主要操作进入现有顶部栏，内容区不重复占用标题行，报告正文紧接顶部栏；加载期间也不重复显示页面标题，但保留加载状态和失败后的错误标题及恢复操作。切换页面或从任务报告返回旧审核轮次后，隐藏页面的操作不能留在顶栏，前进仍恢复原报告。可访问标题继续保留，规则目录的重复总数移除；分页总数、已选范围和异常反馈不变。规则上传仍由 `test_rule_interactions.py` 的受控 HTTP 流程验证原件、草稿、人工核对与保存，移动按钮不能改写这些操作边界。

`test_audit_preview.py` 的历史返回回归要求：旧 Round 可见时，缓存 Task 的定时观察、聚焦和网络恢复不能发起后台读取；浏览器前进回 Task 后仍读取当前结果。测试使用受控读取与浏览器时钟，不推进真实审核或证明服务端执行进度。原单请求、失败退避及迟到读取保护继续由相邻观察回归覆盖。

`test_round_reports.py` 使用原生页面与受控 HTTP，核对历史 Round 从已授权的真实 Task 关联打开原 PDF、下载固定字节，返回保留文件、条款、筛选与专注复核；生成中、失败、无来源 Task 及报告权限拒绝分开表达，读取不创建或恢复工作。受控同源宿主只承载已有导航消息，核对内嵌原生页面打开报告后，宿主刷新仍恢复同一报告；不据此声称当前预览宿主已有该历史入口。`tests/integration/product/test_audit_tasks.py` 在隔离 PostgreSQL/Worker 中核对同一 Round 与 Task 的报告投影、冻结结果不变、撤权及跨身份拒绝；原 Round 可读不等于私有 Task/PDF 可读，缺少 Task 关联不伪造任务或报告。浏览器受控响应不代替这些持久化与授权检查。

公开 `FindingLocator` 保留已有可选 `kind` 字段；`test_openapi_contract.py` 验证 `file_version` 定位经类型校验后不丢字段，同时兼容未带该字段的旧记录。历史与 Task 投影的完整比较不应靠两边同时过滤字段来通过。

其中的未知提取恢复用例从当前审核要求上传入口进入：刷新和核对只读原请求，记录 HTTP 方法以拒绝恢复期间的写操作；重新提取需确认可能的模型费用并创建新请求。原请求的迟到读取不能替换新请求；挂起的提交响应晚于人工录入返回时不能覆盖人工内容。`RuleExtractionRecovery.tla` 是一份冻结来源、两个请求及延迟响应的有限 Safety 检查；正向配置检查未知不冒充运行中、只读不重提、当前请求及人工录入边界，四个故障配置保留对应反例。它不证明 SQL 持久化、跨标签页恢复、授权、模型质量、真实收费或最终完成。

批量上传恢复从当前“上传资料”和草稿内“添加审核资料”进入。`test_rule_interactions.py` 控制逐文件回执、保存拒绝和迟到读取，要求已收到文件保留、未发送与未知分开、查询不新建传输，以及离开后的结果不写入另一草稿。`test_upload_recovery.py` 通过真实 Product API/数据库上传三份 XLSX，分别中断第三份已完成传输的回执、或传输前已完成草稿保存的回执；清除本机记录后读回同一草稿，查询或显式继续原请求完成关联。继续操作固定原请求 ID 和文件摘要，复用现有幂等创建，不把“未查到”当作新建身份的许可。测试不启动审核或扣费；对象存储的实际持久性另由运行环境验证。`UploadBatchRecovery.tla` 是两个页面 owner、三份文件的有限 Safety 检查，保留丢失成功文件、新建未知请求及迟到提交的故障配置；同身份创建/写入的幂等性由 API/存储边界验证，模型不证明完整系统 refinement、跨标签页并发、SQL/对象存储持久性或最终完成。

新版首次上传使用已有服务端 Draft 保存准备资料，随后以 revision 比较提升为同一 Task；准备不创建 Round 或审核工作。`test_native_upload_preparation.py` 经原生页面与受控公开 HTTP 检查保存先于传输、刷新后读原请求、混合成功／未知、原件内容核对和提升后恢复；不证明真实飞书、数据库耐久性或审核质量。`test_audit_tasks.py` 的对应 PostgreSQL 场景验证实际 Draft 保存、授权、冲突和提升，须在具备数据库的环境执行。`NativeUploadPreparation.tla` 仅检查两个页面、两个选择、revision 0～2 的有限 Safety：保存先于外部效果、原身份保留、单次提升和冻结集合；两个故障配置保留未保存即传输和提升后晚到写入的反例。数据库锁与持久性是模型假设，不是完整系统 refinement 或最终恢复证明。

## Web OpenAPI contract

报告分享的物理连接失效回归仍由 `tests/schema/test_audit_bindings.py` 拥有。共用发送生命周期沿用 `ReportShareDispatch.tla`：两个接收人、各两次尝试的有限 Safety 检查；无公平性、状态约束、对称缩减或覆盖，静止终态不按死锁处理，不证明权限、文件内容、无限重试或最终送达。

API owner tests validate that every major Web-consumed endpoint has an explicit successful JSON response schema and that JSON write endpoints have an explicit request schema. Repository CI then installs the frozen Web lockfile inside `apps/web`, runs `pnpm api-types` there, and fails if `apps/web/src/generated/openapi.ts` differs from the committed artifact. Web CI typechecks/tests/lints/builds the generated consumers.

## MCP

`tests/integration/product/test_mcp.py` 通过真实 PostgreSQL、API 数据库角色、OAuth 和 MCP HTTP 协议验证授权码单次兑换、刷新重放撤权、Scope、文件字节、对象权限、人工确认、原请求读回和新 API 实例恢复。复核场景在实际数据库提交后丢弃传输响应，验证同事务回执及原请求重放；网页并发修改后旧确认不能覆盖新复核。对象存储、隔离身份及识别模型元数据为受控边界，不能据此声称完成真实飞书、客户文件或外部客户端验收。

`McpDelegation.tla` 只建模一个授权、两代刷新令牌、一个操作身份及两份载荷。正向配置检查单次效果与执行前授权；`-replay`、`-binding`、`-revocation` 分别保留未知结果重发、确认换绑和撤权后启动的反例。无公平性、状态约束或对称缩减，不检查最终完成，静止终态不视作死锁。它假定 claim 与令牌轮换原子化；SQL 并发测试必须独立证明这一假设，连接上下文本身不是证明。密码学、对象 ACL、可变事实的事务绑定和真实外部效果仍由对应实现与验收负责。

`ProviderRefresh.tla` 是共享飞书续期的独立 Safety 检查：两个授权身份、各两代令牌、一个浏览器和两个客户端。模型保留 claim 与结果之间的丢失、延迟及新登录；检查每代最多一次刷新、结果不写入另一授权。正向为 20 个状态、深度 6；重复 claim、未知后重试、错误 owner 的故障配置分别在深度 3、4、4 违反预期不变量。没有公平性、状态约束、对称缩减或覆盖；静止状态不作死锁，不证明最终续期或真实服务商行为。模型中的 claim 包含外部发送边界；实现允许重试的连接建立失败必须证明请求尚未发送。新登录使用不同凭据；相同凭据别名的唯一性由数据库约束和实际测试负责。

MCP 集成测试通过真实 PostgreSQL/HTTP 与受控飞书响应，验证 Web/多个客户端共享一次续期、未知状态跨 API 实例保留、过期 claim 不重试、旧响应与新登录隔离、同凭据并发归属，以及续期期间撤权仍生效。`services/api/tests/test_login_recovery.py` 区分连接建立失败与读超时/无效响应；后者不能假装可以重用旧刷新令牌。`tests/schema/test_provider_authorizations.py` 从旧 schema 迁移有数据的会话和 MCP 委托，核对精确绑定、歧义拒绝、未知结果保留、重复迁移及 Worker 无权读取。这些结果不代替真实飞书续期与现网迁移、回滚验收。

# 开发

普通工作只需要当前 owning Issue、相关源码和最近测试。Issue 表达验收结果，不表达实施顺序。

组织内资产的默认使用规则见 [AGENTS.md](../AGENTS.md)，这里不重复权限政策。

## 当前配置

应用配置的 executable authority 是 typed Settings、[`.env.dev`](../.env.dev) / [`.env.production`](../.env.production) 里的默认值，以及 [`.env.example`](../.env.example)。

配置值按显式 `Settings(...)` 参数、进程环境、显式指定的 dotenv 文件、默认值依次取值。代码字段名和环境变量别名遵循同一优先级；显式 `False`、空字符串、空来源列表不能被较低优先级的值替换。`pydantic-settings` 最低为 2.12.0，以使用其别名合并修复；不在本项目另建配置合并层。

- Secret 只通过本地 gitignored `.env`、GitHub Secrets 或部署 Secret 注入。
- Deep 可独立设置 `PPAP_MODEL_CODING_BASE_URL`、`PPAP_MODEL_CODING_MODEL`、`PPAP_MODEL_CODING_API_KEY`，base URL 与 model 必须同时提供；专用 API key 只给 Controller。API、Worker 和 Controller 使用相同的非敏感策略；未设置时沿用 `PPAP_MODEL_TEXT_*`。这些覆盖值不改变 Standard 模型。Pi 通过现有 Responses 网关使用短期执行授权，真实 provider key 不进入 guest。
- 已提交的 `.env.dev` 填 isolated-dev Settings 默认值；[`.env.production`](../.env.production) 填生产仍可沿用的默认值，口令、S3 endpoint、公开 URL 和 LLM 留空。生产不能沿用 isolated-dev 口令和 `http://127.0.0.1:9000`。
- 生产 compose 用 Docker Compose 的 `--env-file` 注入。CI fixture 是 `deploy/compose.env.example`。真实部署把填好密钥的文件交给同一个 `--env-file`。`init-db` 读取 bootstrap/migrator 口令，先用 `psql` 执行 `migrations/bootstrap.sql`，再通过 `sh /migrations/apply.sh` 按 `NNNN_*.sql` 文件名顺序执行 product migrations；`init-bucket` 用 Silo `mcli` 确保对象桶。API/Worker 只插值各自拥有的字段（含选定的 `PPAP_PG_HOST`/`PPAP_PG_PORT`，以及 API 的飞书变量），不含 bootstrap/migrator 或对方角色口令。
- isolated-dev Compose 默认关闭业务收费，避免无飞书授权的浏览器流程测试调用真实计费；需要验证收费时显式设置 `PPAP_USAGE_CHARGE_ENABLED=true` 并提供额度接口。此默认值不改变生产 Compose 或 Settings 的收费政策。
- 把 API/Worker 接到已有的 self-hosted InsForge PostgreSQL 时，用 `docker compose -f deploy/insforge/compose.yaml --env-file deploy/insforge/env.example up -d`。该 overlay 不启动 postgres，加入外部 `insforge-network`。Custom Compute 规格在 `deploy/insforge/compute/`。
- `.doc` 转换是默认行为，没有开关变量。
- 可选 capability 缺配置只影响该 capability，不阻止无关开发。Deep 由 `PPAP_DEEP_CONTROLLER_SOCKET` 表示是否配置；未配置时 Core 仍可启动，但审核执行明确失败，不回退。Live Deep 用 `deploy/isolated-dev/deep-live.yaml` 挂 UDS。Controller 工厂要求显式选择 `PPAP_DEEP_RUNTIME=container-only`，并将 `PPAP_DEEP_SANDBOX_PROFILE` 设为 `off` 或 `ppap`；这些运行配置不进入 Worker RPC，guest 命令和路径由可信 Controller 管理。客户规则的检查程序由平台离线准备，产品内不生成、不修复检查程序。开关、sidecar 启动和 UDS 可用均不证明客户审核质量或强隔离，具体限制见 [Deep 部署说明](../deploy/deep/README.md)。
- 飞书任务通知由 API 进程发送，`PPAP_FEISHU_NOTIFICATIONS_ENABLED` 默认 `false`；开启还需配置该应用的 ID、Secret 和有效授权。`GET/PUT /audit-tasks/{id}/notification` 读取、保存创建人的完成通知设置，修改必须带 `expected_revision`；该设置只控制完成通知，不控制待确认或开始审核通知。通知未进入发送边界前可修改，进入后只读回状态。发送结果未知不自动重发；这些接口不证明飞书已送达或已读取。
- 已授权的单任务通知联调可调用 `dispatch_task_notification(..., task_id=...)`，范围在领取工作及整理过期发送状态前生效；保持常驻 API 的全局通知开关关闭，避免顺带处理历史队列。历史补发须另行授权，仍使用已有意图、阶段校验和回执，不重置已发送或未知状态。
- 机器人已启用不代表具有发送权限；最小发送权限为 `im:message:send_as_bot`，须在发布及租户授权后实际生效。明确的飞书 2xx／4xx 非零错误码记为发送失败；超时、5xx 或无有效回执仍为未知。联调以真实消息 ID 和客户端可见性核对送达，不为此扩大聊天读取权限，也不把发送成功当作用户已读。
- 报告附件上传使用 `im:resource`。真实接口错误仍可能列出旧的 `im:resource:upload`，但控制台不可新增该项；不能只照错误文字申请权限。资源权限不等于读取聊天消息权限；机器人或链接发送可用也不证明附件上传可用。
- 组织分享按能力验收：向实时 `user_info` 核验的本人发送链接，不依赖群目录或消息附件权限；群组选择仍核对机器人所在群及内部组织边界，PDF／ZIP 附件仍走消息文件上传。目录未授权与上传未授权只限制对应能力，不改成允许未知接收对象，也不阻塞已有报告预览或网页下载。附件准备阶段被权限拒绝时没有发送消息；授权恢复后读回不自动重发，只能显式恢复原请求的失败目标。
- 权限发布与安装组织授权分别核对。[查询租户授权状态](https://open.feishu.cn/document/application-v6/scope/list)返回的权限必须满足 `grant_status=1`；`2` 是未授权，不能仅凭名称出现在列表中认定生效。[获取用户信息](https://open.feishu.cn/document/server-docs/contact-v3/user/get)还区分接口权限和字段权限：`contact:contact.base:readonly` 可使接口成功，但不保证姓名或账号状态可读。缺字段时接收资格保持未知，并核对字段权限及通讯录数据范围；不能把 HTTP 200／code 0 当成完整组织分享验收。
- 新版通知卡片设计采用[卡片 JSON 2.0](https://open.feishu.cn/document/feishu-cards/card-json-v2-structure)，显式声明 `schema: "2.0"`；正文组件放在 `body.elements`，使用 `markdown`，按钮使用 `behaviors`，不混用 1.0 的 `div.text`／`action` 结构。2.0 要求飞书客户端 7.20 及以上；动态文件名等内容须先按[官方富文本规则](https://open.feishu.cn/document/feishu-cards/card-json-v2-components/content-components/rich-text)转义。JSON 语法检查或设计预览不等于发送及送达验收。
- 同任务 ZIP 通知链接使用 `/audit?task=...&report=1&packageRequest=...`；跨任务包使用 `/audit?view=archive&packageRequest=...`，两者均经过当前身份入口。报告中心和档案页通过既有 `/audit-report-packages/{request_id}` 读取固定集合，失败只读回原请求，不自动创建新包；档案页在发送创建请求前保存该地址，刷新后只读恢复。`reports-zip-link` 是显式预览场景，不证明真实飞书送达。
- 卡片直接确认需配置 `FEISHU_VERIFICATION_TOKEN`，并将新版 `card.action.trigger` 回调订阅到现有 `/integrations/feishu/events`；不使用旧版回调结构。仅配置 Token 不证明平台订阅已生效。回调通过原消息 ID 和当前创建人身份恢复同一确认事务；消息发送未知或内容被截断时只能打开页面。当前支持 HTTPS 下的 Verification Token 校验，不接受加密回调；真实回调和三秒响应要求仍须在客户等价环境验收。
- `app_ticket` 通过新版 `POST /integrations/feishu/events` 独立接收，使用现有 `FEISHU_VERIFICATION_TOKEN` 与应用 ID 校验，保存至自己的 PostgreSQL；不调用 UAC、不读取旧 Redis、不增加转发或轮询。沿用 HTTPS + Verification Token 模式；配置加密回调前必须另行支持并验证解密，当前遇到加密体明确拒绝。事件 P1 `ts` 为秒（最多十八位小数），P2 `header.create_time` 为毫秒，统一为 Decimal 秒并以 PostgreSQL numeric 保存，避免浮点或微秒舍入丢失顺序；缺失、非法或超过本机时间五分钟的时间戳拒绝。单条 guarded upsert 只接受更晚的事件，重复、迟到或同时间不同值不替换，也不刷新接收时间；时间相同无法判断新旧，须观察后续推送或显式请求重推，不自行给 ticket 排序。应用 ID 存在冲突则拒绝。数据库成功提交后才返回成功，响应丢失可安全重投。
- 旧回调尚未切换时，已授权的人工联调可只读共享 UAC 当前票据；这不是新版运行时回退。先核对 UAC 与目标应用身份，再确认实际键名、键和值的编码：普通字符串键与 Java 序列化键可以同名并存，旧字符串值不能证明 UAC 正在使用它。票据须通过真实应用／租户 token 交换验证；后续权限还要独立读取实际授权并调用对应接口。只读诊断不写旧缓存、不改旧回调，也不因缓存存在或 token 可用就宣称分享权限已经生效。
- `GET /capabilities` 的 `app_ticket` 只表示本地是否有记录，不证明票据仍有效、定时推送已连通或能够调用飞书；`app_ticket_event_timestamp` 是秒级 Unix 时间戳字符串，`app_ticket_received_at` 是 ISO 写入时间，均不含密钥，重复事件不能刷新它们。迁移保留原票据，但不伪造其事件时间；未带事件时间的历史初始化不能覆盖已经由回调更新的记录。正式切换与连续更新验收见 [TKE 配置](../deploy/tke/README.md#飞书回调切换尚未执行)。
- 预览切流用 `PPAP_CURRENT_TENANT_ALLOWLIST`（逗号分隔的 Feishu `tenant_key`）。空名单不限制。生产目录写入海岸线租户。`PPAP_USAGE_CHARGE_ENABLED=false` 时受理不写 `usage_events`；预计点数按匹配文件和标准单价计算，不是已扣费回执。
- 新增配置项前先确认它代表独立运行决策，而不是可推导内部细节。

本地开发也可作为现网的本地客户端：本地 Web 通过产品 API 使用同一份真实数据；需要验证未部署的服务代码时，本地候选 API 可连接现网 PostgreSQL、对象存储与飞书。浏览器不直连数据库，租户与对象授权仍由 API 执行。共享数据不代表新旧代码或 Schema 自动兼容，连接前须核对候选所需接口与数据库结构；这种联调不运行初始化、迁移、测试重置或额外 Worker。写入仍是真实业务操作，不能因入口在本地而视作模拟；会产生收费、通知或审核受理的动作须按本次联调范围控制。

## 原件事实确认

审核结果中的原件事实问题与 Deep 规则含义澄清分开保存。`/audit-rounds/{round_id}/fact-answers` 只允许当前租户成员且有本轮复核和全部冻结原件读取权限的人使用。回答绑定轮次、文件版本、字段和所见问题；一个回答事务记录单独的人工确认结果，不覆盖机器结果、不入队新审核、不消耗模型额度。

当前可确定性重算的是 UAES 单页 PDF `visual_result` 已冻结的全满足布尔检查。原生 `field()` 能唯一定位到未读值单元格时也会产生文字问题，但没有完整复算依据时只保存回答，结果仍为 UNABLE；多页和其它 Checker 尚不借此推定通过。未知答案保留 UNABLE；其它依赖已确定的条款不再追问。已经记录的回答不可原地改写。

界面在当前结果和历史中并列显示机器结果与人工确认。`fact-answers/report` 下载单独的人工确认 PDF，包含回答人、时间和原件位置；既有机器 PDF 保持不变。`fact-answers/export` 提供同一记录的 JSON，明确标为不可自动用作训练样本。

## 按规则读取审核证据

所有适用审核使用同一流程：规则确定所需事实 → 原生提取 → 按具体缺口使用 LLM OCR → 固定程序/有来源的文本语义检查。没有新旧流程开关，不把发布批准变成运行时分支。

- 原生 Excel、PDF 文字和可靠表格优先。非扫描 PDF 的表格审核先走 PDF→Excel，保留原 PDF SHA、页码、单元格和区域映射；没有可靠转换或页面未覆盖时，不能退到 OCR 猜表格并报告通过。空的 OCR 结果不证明没有内容。
- 印章、签字、实际勾选、照片及复杂图形事实使用原图和专用 OCR 模型。PSW、V4、检具、尺寸和通用图像规则复用同一事实入口；专属规则用代码判定，通用规则将可定位观察交给文本语义检查。
- `PPAP_MODEL_TEXT_*` 是文本配置。`PPAP_MODEL_VISION_API_KEY`、`PPAP_MODEL_VISION_BASE_URL`、`PPAP_MODEL_VISION_MODEL` 是独立 OCR 配置，缺少时不能借用文本配置。OCR 事实提取默认使用 `reasoning.effort=none`（关闭推理）；推理强度与输出上限是能力约束，不保证服务必定成功。可选 `PPAP_MODEL_VISION_EXTRA_BODY_JSON` 只给 OCR；显式 `reasoning` 原样优先，实际参数进入配置摘要。Secret 仅通过现有进程环境注入，不写日志。
- OCR 复用 `documents.ocr.client` 的 OpenAI Responses 调用，`store=False`、严格 schema、单次 HTTP connect/read/write/pool timeout 20 秒、零自动重试；没有整页或整份文件的总墙钟预算。持续接收数据可能超过 20 秒。视觉事实每次最多 4096 tokens，包含推理与可见 JSON；截断或空白结果为技术失败，不解析残缺证据。
- 同轮同源页及同组事实复用成功/失败回执。缓存绑定原件、原页、模型/端点与提取配置；原件或配置改变不能覆盖冻结依据。局部未知保留来源，不抹除其余可靠原生内容。
- 扫描 PDF 优先级低，当前不自动做整份表格转录或多页补格。没有可靠原生表格时明确返回无法检查；仍可由具体条款请求指定页/区域的视觉事实。不要为补齐工作簿触发整份 OCR。
- 文本模型只接收相关条款和有原件位置的片段，引用须回查；部分范围不能声称全篇通过。不能用语义分析补造视觉内容或替代缺失 AP 标准表。
- 实际 OCR SDK 回执并入现有审核用量；有回执才记录 token、供应商费用及耗时，缺回执为未知。供应商费用不是客户业务收费。原件文字、模型隐藏推理和密钥不得写入诊断日志。

Excel 的图形事实通过 `audit.engine.checkers.excel_field_evidence` 读取。XLS 先经既有 LibreOffice 隔离转换为 XLSX，保留原始绘图部分；单元格、控件和可靠原生文字优先使用。按字段文字、合并范围和相邻字段确定区域，必要时由 `documents.excel.visual` 渲染；Excel 与 PDF 共用 `pdf_field_region` 的栏目边界。签字字段不读取印刷姓名、日期或客户签字，保证书盖章与授权表盖章分别取证。缺少标签且需要证明没有盖章时，覆盖完整适用工作表，包含原生单元格范围外的绘图；不能凭局部未找到就确认不存在。模型只返回事实，坐标、文件版本和来源摘要由程序保存；未知仍为 UNABLE，调用或解析错误为 ERR。同一字段复用成功或失败结果，不自动重试。

UAES 目录叶子的条款在各自叶子中实现，只从 `checker_lib` 复用原件读取和取证，不调用其他版本的业务类。PFMEA 经验教训、评分描述及 CP 追溯/FIFO 措施使用有原文引用的文本语义检查；数字、日期及跨文件身份由代码检查。原图只用于实际图形事实。通用视觉读取最多十页或十个工作表；单页/单表读取不能证明多页全图关系。当前跨页仅支持调用者明确声明的“存在”事实，完整范围都为否才支持否定；全图一致性、所有签章齐全等联合事实在多页时保持未知。DOCX 的原生内容可以读取，尚无可靠位置的图形事实返回 UNABLE。扫描表格不补做整份转录。原件或模型配置变化、调用失败与事实未知分别报告，不自动重试或转成 PASS。

尺寸审核把检查数据、判定标准、单位和结果绑定到同一原始记录。明确的不同量纲不能直接比较；支持的同量纲单位先换算，单位无法可靠确定时返回 UNABLE。跨文件读取绑定本轮冻结文件版本，完整匹配零件标识及工序号，保留两份原件的单元格位置。不同文档的修订计数不要求相同；两份原件都明确声明同零件的工程更改水平时，冲突不能通过。身份不足不能支持 PASS，也不借用相近编号的记录。历史文件、规则与结果不变。

离线回放和真实调用分别报告。人工样本答案只作差异参考，不是绝对真值；不能为匹配标签把未知硬改为 PASS/NG。确定的误判、权限和数据完整性仍是必须修复的问题。当前实现测试与真实效果验收入口见 [测试入口](../tests/README.md)。

## 当前命令

提交前在本仓库执行一次 `uv run --no-sync pre-commit install`。之后每次提交对暂存的 Python 文件运行与 CI 相同的 `ruff check --fix` 和 `ruff format`。`mypy` 仍只在 CI 的 Repository contract 中运行。

数据库 migration ordering authority 只有 numbered filenames。Compose / InsForge / TKE / Schema CI 共用 `migrations/apply.sh`；仓库不存在 `apply.sql` 或第二份顺序清单。

TKE 的 Kubernetes authority 是 `deploy/tke/kustomization.yaml`。部署入口是[原生部署命令](../deploy/tke/README.md#authoritative-release-procedure)，直接使用 `kubectl` 更新并重启；默认先执行更新，遇到错误再诊断，不运行 Python 部署编排或自动预加载。构建由 `build-current.py` 独立负责。CI 通过 `render-current.sh` 使用固定版本 Kustomize 生成清单并执行 kubeconform。edition proxy 的 nginx 内容只存在于 `deploy/tke/edition-proxy.conf`，ConfigMap 由 Kustomize `configMapGenerator` 生成。

测试归属、各组运行命令、Distribution closure、CI 分组与默认 merge contract 见 [测试入口](../tests/README.md)。本机 PostgreSQL 集成测试可加 `--local-postgres`：创建并清理本次独占的临时 cluster（默认用 PATH 上的 postgres，macOS 备用 Homebrew postgresql@17，或用 `PPAP_TEST_PG_BIN` 指定 server 目录），不接入共享开发数据库，无需 Docker；依赖缺失会失败而不是 skip。

GitHub Actions 的外部模型测试使用 `CI_LLM_API_KEY`、`CI_LLM_BASE_URL` 和 `CI_LLM_MODEL` 三项仓库 Secret，与生产配置分开；真实模型用量属于测试运行，不以缓存伪造模型成功。

## POC 规则资产导出

原始 DOCX 是规则资产的内容依据；[`POC Rule Assets`](../assets/nio/history/poc规则/AGENTS.md) 规定生成文件和媒体路径。仅在需要重新生成资产时，从仓库根目录执行：

```sh
cd assets/nio/history/poc规则/markdown
pandoc '../10规则-控制计划.docx' --wrap=none -t gfm -o '10规则-控制计划.docx.md' --extract-media='media/10规则-控制计划'
```

在输出目录运行 Pandoc，使图片链接保持 `media/10规则-控制计划/media/image*.png`，对应文件位于该目录的 `media/10规则-控制计划/` 下。生成后检查 diff、所有图片引用和对应文件；只移除已确认不再被引用的旧生成图片，不手改规则正文。若内容变化会影响预期行为，按 [Corpus 测试入口](../tests/README.md#corpus) 验证 v3 oracle 和规则资产边界，不恢复旧仓的测试路径，也不据导出成功声明审核通过。

## 工程工具链

当前实现是 Python 3.12、uv workspace（每个 production owner 一份 `pyproject.toml` + 统一 `src-layout`）、已提交的 `uv.lock`，以及 `apps/web` 的 pnpm/TypeScript。质量检查是 `git show --check` 与 `compileall`；Python 测试统一由 pytest collect/execute；现有 `unittest.TestCase` 仅作为测试类写法保留；前端白盒/契约测试是 `pnpm test`；跨栈浏览器 E2E 是 Python Playwright；镜像是 Docker Bake；运行时是 Docker Compose 或 InsForge native lifecycle（`@insforge/cli@0.2.8`）。

CI 以项目原生 metadata / lockfile 为 authority，不再另写一套安装语义：

- Python 依赖：`uv.lock`。`dev` 是默认 dependency group，Repository contract / Python modules / schema / corpus 使用 `uv sync --locked`。Product integration / Playwright E2E 另加 `--group e2e`。安装之后用 `uv run --no-sync` 执行命令，避免 `uv run` 再 sync 一次。 Distribution closure 不复用这个完整 workspace environment：它构建目标 distribution 的 workspace wheel closure，并在 clean venv 中显式安装；registry dependency 版本仍由 `uv.lock` 生成的 constraints 固定。
- Container build graph：`docker-bake.hcl` 是 API / Worker / Web Dockerfile、context、build args 与 output tag 的唯一 authority。isolated-dev、production、InsForge、remote-dev Compose 只消费 image；本地、remote runner 与 CI 都先运行 `docker buildx bake`，再用 `docker compose ... up --no-build`。`scripts/verify-container-graph.mjs` 只读取 `docker buildx bake --print` 与 `docker compose config --format json` 的 native output 做 PPAP image-identity policy。
- Python 解释器：`.python-version`（3.12）。`pyproject.toml` 的 `requires-python = ">=3.12"` 是兼容范围。
- Node：`.node-version`（22）。workflow 通过 `actions/setup-node` 的 `node-version-file` 读取。
- pnpm：`apps/web/package.json` 的 `packageManager`。`corepack enable` 后 Corepack 只从当前目录向上读取该字段，不看 `pnpm --dir`。因此 workflow、Web Dockerfile 和 isolated-dev Vite overlay 都在 `apps/web` 里启动 pnpm，不再重复 `pnpm@…` 字面量。安装仍是 `pnpm install --frozen-lockfile`。CI 的 pnpm 消费 job 在 Node setup 前通过 `GITHUB_ENV` 设置 `COREPACK_HOME=$RUNNER_TEMP/corepack`，让 Node setup 与后续命令共用当前 runner 用户可写的缓存，不依赖系统目录写权限。
- GitHub Actions：第三方/官方 action 钉到不可变 commit SHA，行尾注释写清已解析的 tag（例如 `actions/checkout@11bd719… # v4.2.2`）。本地 composite action 用路径。当前 SHA 来自 GitHub 上对应 release tag 的剥皮 commit，不是编造的 hash。
- uv 二进制：由 `astral-sh/setup-uv` 的 `version: "0.9.26"` 引导安装。这是 bootstrap pin；安装 uv 本身必须发生在读取 `uv.lock` 之前。

有意保留的重复（消除它们需要模板化 Dockerfile 或另做包装层）：

- `deploy/app/Dockerfile` 继续用 `python:3.12-slim-bookworm`。uv 来自钉死 digest 的 `ghcr.io/astral-sh/uv:0.9.26`。生产安装是镜像内 `uv sync --frozen --no-dev --no-editable`，不设置 `PYTHONPATH`，也不用 pip。

  第三方依赖先仅凭根 metadata/lockfile 安装（`--no-install-workspace`），再复制源码并安装非 editable 工作区包；两步共用 BuildKit uv cache mount，下载缓存不进入镜像层。`.dockerignore` 排除各层测试目录，测试改动不影响生产构建缓存。缓存跨云效 runner 的复用由构建平台决定，不能保证 cache mount 在新 runner 上仍存在；现有 inline layer cache 保留。
- `deploy/web/Dockerfile` 的 `node:22-alpine` 与 `deploy/isolated-dev/web-dev.yaml` 的 `node:22-bookworm` 仍写在镜像 tag 里。
- `deploy/deep/package.json` 与 `package-lock.json` 固定 `@earendil-works/pi-coding-agent@1.5.0`。Deep 镜像使用固定 digest 的 Node.js 22.22.0、`npm ci --ignore-scripts`（完整性由 lock 中的 integrity 校验），并执行 `pi-runner.test.mjs` 的离线协议测试。运行时版本检查与执行修订使用 `audit.policy.PI_VERSION`，须与部署依赖版本一致。

所有 GitHub Actions 使用 GitHub 托管的 `ubuntu-24.04` runner，每个 job 使用默认的临时 checkout。CI 不依赖自托管主机、共享 checkout 或 runner 名称。CI 依赖 capability，而不是发行版。workflow 可以安装 uv/Python、Node、Corepack/pnpm 和项目依赖；不得用 `sudo` / `apt` / `yum` / `apk` / `systemctl` 修复 runner，也不得安装或启动 Docker。Python modules 的 services variant、Schema、Corpus PSW、Product integration 和 InsForge lifecycle 要求 runner 已提供 Docker Engine、Compose 和 Buildx；缺任一能力时 fail-fast 输出 `runner does not satisfy ppap-docker capability`。Web、无 PostgreSQL 的 Python modules 和普通 Corpus 不要求 Docker。Repository contract 需要 Docker Engine、Compose 和 Buildx：`docker compose config`、`docker buildx bake --check`/`--print`、nginx 黑盒、Kustomize 与 kubeconform。`actionlint` 1.7.7 检查 workflow 语法。InsForge static config 仍由 `verify-config.mjs` 调用 `docker compose config`。live InsForge lifecycle 需要 `local start` 使用的 runtime Docker capability，只在 InsForge 相关 path、手动 dispatch 或每周 schedule 上运行。LLM / Feishu readiness 是真实外部 probe，手动 dispatch 或每周 schedule，不进 `CI Gate`。Product integration 对同一 `PPAP_BUILD_SHA` 只 `docker buildx bake` 一次，生产 runtime/backup 与 isolated-dev E2E 复用这些镜像（E2E 使用 `compose up --no-build`）。PSW 是 Corpus matrix 的 purpose-built container variant，单独 `docker buildx bake worker` 后从 worker 派生测试镜像。CI Compose 使用 `ppap-ci-<purpose>-<run_id>-<run_attempt>` 作为 project name，cleanup 在独立 `if: always()` step。现有 Compose stack 仍 bind-mount 仓库内 `migrations/`，因此 services、Schema、Product integration 和 InsForge lifecycle 需要 Docker daemon 能看到 runner 使用的同一份仓库路径。后续若把 Ruff、Pyright 或 Vite 收进来，直接调用原生 CLI，不另起 PPAP dispatcher。

Product integration 的 runtime/backup 与 E2E 通过 `bash scripts/run-compose-tests.sh <pytest-directory> <compose-options>` 启动：先由 Docker 分配 loopback 临时端口，再固定实际端口并同步 API/Web 的公开地址后执行测试，备份恢复仍使用同一组地址。`PPAP_API_HOST_PORT`、`PPAP_WEB_HOST_PORT` 只控制宿主映射，默认仍为 `8080`/`4173`；脚本不清理其他任务或常驻服务，workflow 保留失败诊断及本任务的 `always()` 清理。

## Web wire contract

FastAPI/Pydantic OpenAPI is the machine authority for Web JSON wire shapes. The Web does not maintain a second DTO registry.

The generated TypeScript artifact is owned by the standard `openapi-typescript` CLI pinned in `apps/web/package.json` and `apps/web/pnpm-lock.yaml`. From `apps/web`, run:

```sh
pnpm api-types
```

That command serializes the real FastAPI `app.openapi()` schema and pipes it to `openapi-typescript --alphabetize`. CI runs the same generation and rejects any diff in `apps/web/src/generated/openapi.ts`. The generated artifact owns wire types only; retry/readback policy, localized error copy, labels, caches, and UI view-model state remain handwritten.

UI-first contracts without a runtime operation are explicit OpenAPI components marked `x-contract-stage: ui-draft`. They share the same generation path, but do not document fake endpoints or imply persistence, matching, billing, or authorization has been implemented. Preview adapters stay outside the production build entries.

具体路由与请求/响应形状以 OpenAPI 为准，行为（请求 ID 幂等、未知回执只读回不重发、对象授权、冻结引用）由 `apps/api/tests`、`tests/integration/product` 与 `tests/e2e` 守护；产品不变量见 [DATA_MODEL](DATA_MODEL.md)。

## UI-first 交互预览

在 `apps/web` 运行 `pnpm preview:workbench`，打开 `http://127.0.0.1:4185/__preview`。预览使用固定 fixture adapter，不代理业务 API，生产构建不包含它；场景只证明界面交互，不证明真实上传、OAuth、解析、匹配、审核、收费或持久化。预览测试在仓库根目录运行（需 `e2e` dependency group）：

```sh
PPAP_PREVIEW_URL=http://127.0.0.1:4185 uv run --no-sync pytest tests/e2e -k preview -q
```

## 本地 Deep checker 实验

`scripts/deep_lab.py` 使用已有人工 checker、资源定义及 Fluent API 开发任务程序。用于平台离线开发检查程序；覆盖 Excel 与电子版 PDF，扫描版不纳入。默认使用 Pi 1.5.0，并从本地 `.env` 的完整 `PPAP_MODEL_CODING_*` 配置读取模型与认证；未提供覆盖时沿用 `PPAP_MODEL_TEXT_*`。先运行 `npm ci --ignore-scripts --prefix deploy/deep`，Node.js 必须至少为 22.19。只有显式 `--runner grok` 才使用旧 `.grok/config.toml`。不要提交认证配置或实验日志。

```sh
uv run --no-sync python scripts/deep_lab.py --recipe checker --seq 11 --effort high
uv run --no-sync python scripts/deep_lab.py --recipe checker --seq 11 --effort high \
  --variant shifted-table --prior /absolute/path/to/artifact.json
uv run --no-sync python scripts/deep_lab.py --recipe checker --seq 11 --effort high \
  --source /absolute/path/to/electronic.pdf
uv run --no-sync python scripts/deep_lab.py --recipe checker --seq 11 --effort high \
  --source /absolute/path/to/fixed.xlsx --rules /absolute/path/to/rule-input.json \
  --expected /absolute/path/to/expected.json --prior /absolute/path/to/artifact.json
```

规则变化实验用 `--rules` 提供完整 `RuleInput` JSON，`--expected` 是独立的 `{条款ID: 状态}` 评分输入，只由宿主读取，不复制给模型；示例输入在 `tests/fixtures/deep_lab/real_rule_changes/`。每次运行在 `output/checker/` 下保留候选、诊断、`artifact.json`、`execution.json` 与独立进程的 `replay.json`；重放不继承模型凭据，完整结果须一致。模型服务 HTTP 错误以 `deep_model_http_<状态码>` 停止，候选不当作已固定产物。

这是本地质量实验，默认使用本地 Pi 进程和现有短期授权网关；不证明产品 guest 隔离或生产端到端行为。固定执行只使用原生读取与明确的 `PdfSpec`，不自动转入 OCR。

## Checker Core 官方发布

官方 Suite 的安装、回归与发布是三个动作。仓库自带的目录叶子已离线验证，`product.seed_standard_suites --apply --publish-defaults` 在入库后直接把每个叶子设为官方默认（`repository_default`，不需要语料证明行），已有官方指针的规则组不会被覆盖，关联叶子先发布；企业未自选时自动跟随，显示为已验证。`product.seed_standard_suites --apply` 只保存 `checkers/<客户>/<规则组>/` 目录叶子的不可变候选，不安装 `standard_suites()` 或历史 generic carrier，也不因相同 `suite_key` 选择旧 carrier。企业 `PUT /suites/{key}/active` 即使带 `force_unverified` 也不能把历史 carrier 设为下一个头；数据库里已有的 carrier 头保持不动，新审核和预检会要求确认目录版本。关联套件的当前头如果是 carrier，企业采用和官方发布同样拒绝，不会改用同名的其他版本。平台以 migrator 角色调用 `SuiteReleaseStore.record_official_validation` 保存独立回归证明，再调用 `publish_official(revision_id, expected_revision=..., expected_validation_revision=...)` 发布。发布前用 `official_active(suite_key)` 读取官方指针修订；首次为 0，验证修订取刚确认的证明。发布在同一事务内重查证明、当前关联 Suite、共享库版本及对象字节，并以 CAS 更新唯一官方指针。过期发布请求须先读回，不自动用新修订重发。验证或保存新候选不会替换当前官方版本；回滚也须显式发布准确旧版本。

首次启用先按关联依赖顺序验证并发布官方 Suite，再受理跟随官方版本的审核。既有 latest 或验证记录不被当作发布批准，未激活的官方源仍可按 revision 查看。没有固定企业版本的租户仅跟随已发布官方指针，企业明确固定某个官方或企业 revision 后不会被后续官方发布覆盖。官方或企业指针若仍指向旧 carrier，新受理和预检拒绝并要求确认版本，不自动改选同 key 的目录版本，也不解除已有 pin。显式发布只影响之后的新 Round。已冻结 Round 继续使用原修订。

## 企业首次采用检查版本

审核规则下有两页：「规则模板」（默认，`/requirements`）按模板组织规则——产品预置规则按客户形成系统模板，上传规则按“所属模板”（规则的客户字段）归入本企业模板，不限定客户的归入「通用规则」；「自动审核」（`?rulesArea=automation`）列出执行自动审核的程序。新建审核在确认规则时可选一个模板，按文件类型为每份文件设置规则后重新识别。

「自动审核」中的程序页（`/requirements?rulesArea=automation&checkerSuite=...`，旧地址 `?checkerSuite=` 仍可打开）可在首次审核前读取可访问候选并明确采用。无企业或官方指针时，读取返回 `candidate_only`、`current_revision_id=null` 和单独的 `candidate_revision_id`，不把候选当作已生效。未验证候选须确认风险，只写本租户指针；先采用直接关联检查，UAES 顺序为 U12/U16 → U07 → U08。已采用版本因依赖变化而过期时，可重新验证，或在同一页面明确确认继续使用未验证版本。上述操作不构成官方发布，不改已有 Round。发布回包未知时只读取原操作，不自动重新发布。

「自动审核」页底部的「系统程序目录」（`checkerPath` 或 `rulesArea=predefined`）按 `GET /suites` 返回的真实路径展开版本、客户和规则组。仓库存在但未安装的叶子可见，只有 `availability=installed` 且带有效官方 Revision 的来源可打开。来源路径与已安装 Revision 分开显示，不用企业当前采用版本替代官方来源。未安装、不可读、身份冲突或扫描失败分别报告，不回退。URL 深链及浏览器前进后退恢复同一选定路径；浏览目录不安装、不采用，也不调用模型。

## 模型来源：平台默认 + 企业 Key

客户不选模型。没有企业 Key 时，审核、识别与规则解释都使用平台模型：文本 `PPAP_MODEL_TEXT_*`，视觉 `PPAP_MODEL_VISION_*`，Deep 编码 `PPAP_MODEL_CODING_*`（未配置时沿用 `PPAP_MODEL_TEXT_*`）。企业配置了自己的 Key（企业 BYOK）后，文本、视觉、编码三种用途全部改用该企业的地址、Key 与模型；企业 Key 不可用时报告不可用，不静默换回平台模型。收费按业务结果计，与模型来源无关。

企业 Key 只由运营设置，客户没有对应 API 或页面：

```sh
python -m product.tenant_models show --tenant-id <uuid>
python -m product.tenant_models set --tenant-id <uuid> --base-url <https url> --text-model <model> [--vision-model <model>] [--coding-model <model>] --operator <name> < key-file
python -m product.tenant_models delete --tenant-id <uuid>
```

Key 只从标准输入读取，不打印。保存前先用该 Key 完成一次文本、一次结构化和一次视觉调用；任一失败则不保存，并列出不支持的用途。地址必须在 `PPAP_TENANT_MODEL_ALLOWED_BASE_URLS`（逗号分隔的完整 HTTPS base URL）内：不跟随重定向、不用环境代理、不接受本机及特殊 IP。API、Worker、Controller 须配置相同的 `PPAP_MODEL_CREDENTIAL_ENCRYPTION_KEY`。Key 与租户、地址、三个模型及配置修订一起加密绑定，复制或改写数据库行不能借用或重定向。

受理时冻结非秘密身份 `{source, provider, model, vision_model, coding_model, revision, deployment}`；之后每次调用都按当前选择重新解析，与冻结身份不一致即 `model_configuration_changed`，不在新模型上继续旧操作。旧版本冻结的个人模型身份返回 `model_contract_retired`。

普通模型请求（文件识别、规则提取/形式化、规则匹配、文字证据、视觉事实）统一经 `documents.ai.run_json`，底层为 PydanticAI 的 OpenAI Responses 模型：文本用 Prompted JSON 输出，视觉用原生 JSON Schema；Codex 端点（`/backend-api/codex`）以流式读取。单次调用最多一次输出校正，不做传输重试，失败只返回代码（`timeout`、`invalid_output`、`provider_status`、`request_failed`）。Schema 合法不等于业务正确，PPAP 仍执行原有业务校验，未知不得 PASS。Token 用量只在 provider 回报时记录，缺失记为未知，不影响结果有效性。

Deep（Pi）只通过本地网关取得短期 Grant，Guest 内没有真实 Key。网关按冻结身份为每个请求选模型：Pi 的请求用编码模型，Suite 程序中的文本与视觉调用分别用冻结的文本、视觉模型，并补上部署选项（如视觉 `reasoning.effort=none`）。每个 Attempt 只有一个请求次数熔断 `PPAP_DEEP_MODEL_MAX_REQUESTS`（默认 200），用于停止失控循环：触发后按资源上限结束。没有累计 Token/Turn 预算，缺少 usage 回执不拒绝有效结果；墙钟时限、容器资源限制与取消保持不变。SDK 可对瞬时错误做少量重试，由请求熔断兜底。

## 自定义规则与 Suite

`POST /rules` 将已授权规则原件发布为企业自己的不可变 Suite，不需要官方基线或预设文档类型。首次发布只保存规则和没有可执行入口的固定占位文件，所有条款覆盖均为 `unsupported`，没有回归证明，不自动调用付费模型，也不复用历史 profile 路由。默认指针、发布记录和 RuleVersion 在同一事务内提交；这是仅发布规则，不是强制执行未经验证的代码。新规则的检查程序由平台离线准备后作为新 revision 提供，客户在产品内不能生成或修复检查程序。

同一前置规则链共享 Suite key。后继规则只保存待发布候选，继承当前采用的 Checker 与资源，保留仍存在条款的绑定，删除条款的绑定移除，新增条款未绑定时为未检查。发布规则不会替换当前检查、起草代码或入队模型工作。新审核继续冻结已采用版本的旧规则与代码，并显示新版规则尚未采用；历史 Round 保持原快照。读取时按条款 ID 比较原文，分别显示新增、删除、正文变化和未变化，不猜测改编号的对应关系。规则页及检查工作区立即提示需要重新验证，旧规则下的原有证明仍只用于旧版执行。请求重发只读回原回执；同一前置规则已有后继时返回 `suite_head_conflict`，不能覆盖并发规则发布。

## 企业 Suite 的官方升级

企业工作区以服务端 `official_update_notice` 显示官方更新提示，显示后通过 `POST /suites/{suite_key}/official-update/seen` 保存当前成员对准确官方 revision 的已读记录。刷新或换浏览器不重复提示同一版本，其他成员和新的官方 revision 独立提示。已读不隐藏升级入口，也不表示同意采用。

`POST /suites/{suite_key}/upgrades` 将所选企业 revision 相对原官方基线的净文件差量带到当前已发布官方 revision。请求固定 source、target、企业发布指针修订与 operation ID；服务端在官方／企业发布锁下校验，原子保存新候选和升级回执。未修改或已还原的文件继承新官方内容，企业修改过的文件完整保留，即使官方同时改过该文件；资源删除及关联 Suite 增删也保留。没有 Python 文本合并或规则语义猜测，不覆盖原 revision，不把最新官方当成企业发布批准。

升级只创建候选，不运行样本或模型；当前采用版本和历史审核不变，候选仍需单独验证和发布。升级响应未知时通过 `GET /suites/{suite_key}/upgrades/{operation_id}` 读回原候选；刷新、读回均不重复升级或自动运行样本。


## 建议复核（确定性提示）

结果页在同一企业、当前用户仍可读取的冻结轮次中，比较同一原件内容、实际执行的 Suite revision 和条款身份。
同文件比较最多读取最近 50 个匹配内容的已完成轮次，每轮最多显示 3 个提示。
提示不调用模型，不改机器结果、人工复核、覆盖或报告资格。尚不启用依赖样本量的统计类信号。

同轮 PDF/Excel 关系由结果页的“关联同一文档的 PDF 与 Excel”明确确认，API 为 `PUT /rounds/{id}/review-document-pairs`。
后端重新检查轮次和全部原件权限，并读取原件验证 PDF/Excel 格式；关系独立保存在轮次元数据，不改冻结执行输入，不按文件名配对。
已关联的文件不能重复分配到另一组关系；未确认或有歧义的关系不产生跨载体提示。

忽略或采纳通过 `PUT /rounds/{id}/review-hints` 保存到已有 finding 的独立回执字段，绑定完整对比对象及两边的 result_attempt。
采纳只保存回执，不启动模型、不改机器结果。`GET /review-hints/responses` 返回当前用户可读轮次保留的采纳/忽略回执计数和比例，分母为已明确处理的提示，不把读取当作曝光。

## Checker 历史身份导入

`python -m scripts.import_checker_history` 只为本仓既有、终态、尚无 Core admission snapshot 的 Round 追加历史身份（`round_inputs.checker_history`，`code_identity=unavailable`、始终 `unverified`）；不创建 Round/Job、不执行代码或模型、不收费、不通知、不改机器结果或人工复核。生产运行须单独授权，不随应用启动执行。参数与安全默认见 `--help`：

```sh
uv run --no-sync python -m scripts.import_checker_history --tenant-id TENANT_UUID        # 只读 dry-run
uv run --no-sync python -m scripts.import_checker_history --tenant-id TENANT_UUID --apply --expect-digest PLAN_DIGEST
uv run --no-sync python -m scripts.import_checker_history --tenant-id TENANT_UUID --round-id ROUND_UUID --verify-user-id USER_UUID
```

缺损记录输出 `blocked` 并返回退出码 2；来源改变后须重新 dry-run，冲突或不确定结果先读回，不清空身份重试。

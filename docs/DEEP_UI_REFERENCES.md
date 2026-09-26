# Deep 审核界面：有界参考

**问题重述：**在现有 PPAP 工作台内，怎样用已具备的组件清楚表达审核进度、条款覆盖、多个证据来源和复核动作，并参考 Beautiful UI 的具体 TSX/CSS 视觉实现？本文件记录交互、数据和视觉源码参考；产品范围以 [DEEP_AUDIT_SCOPE](DEEP_AUDIT_SCOPE.md) 为准。

## 直接可用的表达

- **审核前：**`Workbench` 用单一文件入口、行内审核勾选、目标文件规则选择、模式分段控件和草稿动作表达本轮准备；长规则正文只在规则详情中读。基础控件使用 shadcn/ui，`Card` 与 `Item` 只承担 PPAP 领域组合。[Polar](https://polar.sh/blog/orbit-llm-safe-design-system)
- **审核中：**在现有 `ExecutionState` / `TaskState` 显示真实阶段：读取资料 → 理解规则 → 执行检查 → 验证结果。阶段、最近心跳、耗时来自服务端；只有服务端有条款计数时才显示“已处理 X / 总数 Y”。可离开并回到同一轮。任务行适于显示阶段与状态，但 Beautiful UI 的“Thinking”示例包含推理轨迹，不能直接用于客户审核页；内部思维链和原始工具日志也不应显示。[AI Elements Task](https://elements.ai-sdk.dev/components/task)、[Beautiful UI](https://www.beautifului.dev/#thinking-state)、[本轮范围](DEEP_AUDIT_SCOPE.md#61-进度与概览)
- **审核后：**`RoundOverview` 的机器计数改为“总条款、已判断、无法判断、不适用、未覆盖”；shadcn `Progress` 单独显示人工复核数。技术失败用 `ExecutionBadge` / shadcn `Alert` 给失败类别和可执行动作，不能算作某条规则的业务结论。条款列表显示稳定编号、短标题和文字状态；完整要求在 `FindingReview` 详情展开，接着分列机器判断、原因、来源证据、下一步与人工复核。[现有概览](../apps/web/src/audit/RoundOverview.tsx)、[现有详情](../apps/web/src/audit/FindingReview.tsx)、[本轮范围](DEEP_AUDIT_SCOPE.md#62-结果证据和下一步)
- **多个来源：**在一个条款详情内用现有 `Evidence` 加来源数量和逐条来源：目标或参考角色、文件名与版本、工作表及单元格、PDF 页码或文本块、DOCX 段落或表格单元格、可核对摘录。规则正文来源与执行证据分组；模型解释独立标注，不能充当原文。来源控件“计数 + 展开列表”的模式可借鉴，但本仓不需要安装组件。只有精确定位受支持时才给跳转；否则提供位置说明与摘录，并明确“定位线索”。[AI Elements Sources](https://elements.ai-sdk.dev/components/sources)、[现有预览](../apps/web/src/OriginalPreview.tsx)、[本轮范围](DEEP_AUDIT_SCOPE.md#62-结果证据和下一步)
- **窄屏：**桌面保留 `Workspace` 的列表与原件双栏；平板和手机使用 shadcn `Tabs` 的单面板切换，在“条款列表 → 详情 → 原件 → 返回”之间保留所选条款、筛选、滚动位置与未保存复核提示。现有工作台已有 `useUnsavedGuard`、位置恢复和小屏原件切换；需要把当前下拉选项中的长规则改为短标题与编号，再让列表和详情成为清楚的两个阅读层级。[现有工作台](../apps/web/src/Workbench.tsx)、[PPAP 专属组合](../apps/web/src/ui/domain.tsx)、[本轮范围](DEEP_AUDIT_SCOPE.md#63-响应式与设计约束)

## 最小 API 数据建议

沿用现有 `Round`、`Finding`、`RuleCoverageItem`，只补页面无法从旧字段可靠推导的数据。字段名可按后端实际合同调整。[现有类型](../apps/web/src/api.ts)、[本轮结果合同](DEEP_AUDIT_SCOPE.md#43-结果语义与失败恢复)

| 对象 | 需要的数据 | 用途 |
| --- | --- | --- |
| `round.progress` | `phase`（读取/理解/检查/验证）、`started_at`、`heartbeat_at`、可选 `processed_count` / `total_count` | 真实阶段与耗时；无计数就不显示百分比。 |
| `round.coverage` | `rule_total`、`judged`、`unable`、`na`、`uncovered`；按目标文件版本与稳定条款 ID 列完整条款 | 把完整覆盖与已生成 finding 数分开。人工复核计数另算。 |
| `finding` | 稳定 `item_key`；`status` 扩为 PASS/NG/ERR/UNABLE/NA；沿用 `classification`、`suggestions`、人工 `review` | 分类与条款号组成短标题；完整规则正文留在详情。ERR 与无法判断、业务不符合分开。 |
| `finding.locator.semantic` | `rule_version_id`、`clause_id`、`rule_text`、`outcome_reason`、`scope:{total,checked,unknown}`、`sources[]` | 条款身份、完整要求、判断原因和已核查范围可独立阅读。 |
| `semantic.sources[]` | `file_version_id`、`filename`、`role`（目标/参考）、类型化 `locator`、`excerpt`；规则原件另用 `rule_source_file_version_id` | 在一个结论下核对多个来源；类型化坐标支持精确跳转，其他位置只称线索。 |
| `round.failure` | 从当前 attempt 诊断读取失败类别、用户可读原因、是否允许技术恢复 | 执行失败独立于条款判断；恢复不自动生成新轮次。 |

**不引入：**`json-render` 的用途是由模型生成界面规格；本轮页面和状态由已有产品合同决定，增加这层运行时没有收益。AI Elements 的 `Tool` 组件展示工具输入、输出与运行状态，适合开发诊断，客户页只需有界阶段和可核对证据。Beautiful UI 的流式聊天、思维轨迹、审批卡演示了不同业务，但其 TSX/CSS 的排版、表面、行密度和状态表达可以适配审核页；不因此新增聊天或审批后端。页面基础控件统一来自 shadcn/ui，不新增第二套组件库或模型生成页面。[json-render](https://github.com/vercel-labs/json-render)、[AI Elements Tool](https://elements.ai-sdk.dev/components/tool)、[Beautiful UI](https://www.beautifului.dev/#thinking-state)

## Beautiful UI 全站源码参考（代码核对）

核对基点：[官网画廊](https://www.beautifului.dev/)、[官方仓库固定提交 `44a274e`](https://github.com/slev12397/beautiful-ui/tree/44a274e598395ab61e7c96c26fda2758780253b7)、[组件目录定义](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/lib/meta.ts#L25)和[公开 registry](https://www.beautifului.dev/r/registry.json)。2026-09-26 逐个下载实时官网 registry 的 20 个画廊 TSX，与此提交的同名文件逐字一致；这不等于仓库内已生成的 registry 文件均为最新。以下每项同时给官网定位和固定 TSX；描述是源码事实，不表示本仓已经采用。

| 官网组件 | 固定 TSX | 源码中可核对的结构或状态 |
| --- | --- | --- |
| [Loading State](https://www.beautifului.dev/#loading-state) | [LoadingState.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/LoadingState.tsx#L74) | 3×3 像素点、微光文字和计时；四种外观含视频演示。 |
| [Thinking](https://www.beautifului.dev/#thinking-state) | [ThinkingState.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/ThinkingState.tsx#L93) | 可展开的步骤、推理、搜索和编码轨迹；内置定时序列。 |
| [Streaming Text](https://www.beautifului.dev/#streaming-text) | [StreamingText.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/StreamingText.tsx#L1) | 回答、行内来源、操作及后续建议。 |
| [Approval Card](https://www.beautifului.dev/#approval-card) | [ApprovalCard.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/ApprovalCard.tsx#L1) | 分步提问、单选/自填与 Continue/Skip。 |
| [Tool Chips](https://www.beautifului.dev/#tool-chips) | [ToolChips.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/ToolChips.tsx#L107) | 工具行及差异摘要，展开详情与悬浮预览。 |
| [Task Rows](https://www.beautifului.dev/#task-rows) | [TaskRows.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/TaskRows.tsx#L139) | Capsules 为独立卡；List 是一个卡内的分隔行。 |
| [Chat](https://www.beautifului.dev/#chat-composer) | [ChatComposer.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/ChatComposer.tsx#L84) | 标签页、对话与输入栏；默认回复按定时阶段展示。 |
| [Prompt Bar](https://www.beautifului.dev/#prompt-bar) | [PromptBar.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/PromptBar.tsx#L146) | 圆角/胶囊输入栏、@ 来源、/ 命令、模型菜单；默认自动演示可关闭。 |
| [Recommendation Card](https://www.beautifului.dev/#recommendation-card) | [RecommendationCard.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/RecommendationCard.tsx#L101) | 主建议、三格信号条、展开备选项、局部确认态。 |
| [Context Cards](https://www.beautifului.dev/#context-cards) | [ContextCards.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/ContextCards.tsx#L1) | 来源卡含内容片段、文件类型与来源名。 |
| [Diff Table](https://www.beautifului.dev/#diff-table) | [DiffTable.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/DiffTable.tsx#L63) | 表格改动的色带、行选择与接受态；出现顺序由定时器驱动。 |
| [Records Table](https://www.beautifului.dev/#records-table) | [RecordsTable.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/RecordsTable.tsx#L1) | CRM 式网格、状态标签、排序、选择和列菜单。 |
| [Filter Table](https://www.beautifului.dev/#filter-table) | [FilterTable.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/FilterTable.tsx#L1) | 状态筛选条与共享表格；各行不是独立卡。 |
| [Sidebar Nav](https://www.beautifului.dev/#sidebar-nav) | [SidebarNav.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/SidebarNav.tsx#L202) | 可折叠导航、工作区菜单、聊天搜索。 |
| [Search](https://www.beautifului.dev/#search) | [SearchList.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/SearchList.tsx#L37) | 即时过滤、清除按钮、空结果及滑动高亮。 |
| [Flowchart](https://www.beautifului.dev/#flowchart) | [Flowchart.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/Flowchart.tsx#L329) | 点阵画布、节点与连线，可拖动示例节点。 |
| [Insight Cards](https://www.beautifului.dev/#insight-cards) | [InsightCards.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/InsightCards.tsx#L481) | 分页洞察、迷你图和悬停提示。 |
| [Code Block](https://www.beautifului.dev/#code-block) | [CodeBlock.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/CodeBlock.tsx#L134) | 行号、语法色与统一 diff，可切换/复制。 |
| [Fine-tune Card](https://www.beautifului.dev/#fine-tune-card) | [FineTuneCard.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/FineTuneCard.tsx#L169) | 宽高、圆角、不透明度字段和拖动调值。 |
| [Selection Actions](https://www.beautifului.dev/#selection-actions) | [SelectionActions.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/SelectionActions.tsx#L142) | 贴选中文本的操作栏，思考/流式/结果状态。 |
| [Agent Screen](https://www.beautifului.dev/#agent-screen) | [AgentScreen.tsx](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/AgentScreen.tsx#L182) | 默认占位图，可传入 `streamSrc`；展开屏幕与“录制”计时为界面状态。 |

**样式与复用边界。**[完整 CSS](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/app/globals.css#L1)含 Tailwind v4、`shadow-plugin`、明暗两套 OKLCH 变量及 `@theme inline` 映射；[foundation registry](https://www.beautifului.dev/r/foundation.json)抽出公共 CSS，Records Table 和 Sidebar Nav 另有专属 CSS。源码中的半径为 chip 6、control 8、card 10、window 14 px；浅色使用近白画布、实线细边框及分层阴影，[暗色变量](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/app/globals.css#L74)同步替换。[动画与减少动效](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/app/globals.css#L1439)覆盖渐入、微光、像素灯与光标等。`Grid.tsx` 的初始 272 px 展示高度、`max-w-120` 和居中容器是[官网画廊框架](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/site/Grid.tsx#L64)，不是业务页布局尺寸。

**依赖和许可。**[README](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/README.md#L175)说明单个 TSX 依赖公共样式；[构建清单](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/scripts/build-registry.mjs#L20)列明共享 Button、GlideMenu、EntityChip、ValuePill、Shimmer、StreamText。独立 npm 依赖包括 Button 的 `class-variance-authority`、Prompt Bar 的 `glimm`、Insight Cards 的 `liveline`、Selection Actions 的 `iconoir-react`；Sidebar Nav 使用需单独授权的付费 `@central-icons-react`，可替换图标。项目的[MIT 许可](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/LICENSE)允许复用，但复制实质代码时须保留许可及版权声明。官网公开 registry 只列 20 个画廊组件，当前 `/r/agent-screen.json` 返回 404；第 21 个 Agent Screen 只能按仓库源码查看，不能声称有可用 registry 包。[registry 构建表](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/scripts/build-registry.mjs#L31)也只列 20 个。

**PPAP 取舍。**可把细线、卡片层级、紧凑分隔行、状态色和展开动作适配进本仓 token 与 lint；调整本仓 token 也是可选的视觉实现，不照搬第二套主题。桌面可收紧列表行密度；手机仍需保留适合触控的目标尺寸。Records Table 的大宽度和仅在 hover 显示的控件不能直接移入小屏审核表。[Task Rows List](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/components/primitives/TaskRows.tsx#L181)、[Records CSS](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/app/globals.css#L722)

**演示数据界限。**[Harness README](https://github.com/slev12397/beautiful-ui/blob/44a274e598395ab61e7c96c26fda2758780253b7/README.md#L227)明确用脚本场景生成回复；Loading State 的计时器、Task Rows/Thinking/Tool Chips 的定时阶段、Recommendation/Diff 的本地接受态及 Agent Screen 的“录制”秒数都不能作为真实进度或业务确认。生产状态须由本仓服务端合同提供。

## shadcn Base UI Toggle：状态控件而非说明标签

已下载核对 [Base UI 版 TSX](https://github.com/shadcn-ui/ui/blob/98a1fe67b439324ddc857f47fbdce056600a4329/apps/v4/registry/bases/base/ui/toggle.tsx)、[官网示例 TSX](https://github.com/shadcn-ui/ui/blob/98a1fe67b439324ddc857f47fbdce056600a4329/apps/v4/examples/base/toggle-demo.tsx)和 [Nova CSS](https://github.com/shadcn-ui/ui/blob/98a1fe67b439324ddc857f47fbdce056600a4329/apps/v4/registry/styles/style-nova.css#L1341)。不是 Radix 版，也不能只拿 TSX：基础 TSX 引用语义 class，具体尺寸和选中外观在样式文件中。

- **源码事实：**基础实现使用 `@base-ui/react/toggle`；受控值是 `pressed` / `onPressedChange`。底层输出原生 button 与 `aria-pressed`，另有 `data-pressed` 状态属性，不是 checkbox 的 `checked`。保留原生按钮及可见焦点可支持键盘操作；实际可访问性仍需在产品中验收。[Base UI API](https://base-ui.com/react/components/toggle)、[底层 TSX](https://github.com/mui/base-ui/blob/v1.8.0/packages/react/src/toggle/Toggle.tsx)
- **具体视觉：**Nova 默认透明底，选中后用柔和底色；outline 增加细边框，焦点环独立于选中态。图标与短文字同排；示例通过填充图标加强状态区别。其三档高度是 28/32/36 px，默认图标 16 px；这是参考尺寸，不覆盖 PPAP 的 44 px 触控目标。采用时使用本仓 token、对比度和减少动效规则，不整包复制主题。
- **PPAP 采用：**文件是否参与审核由行内 Checkbox 表达，控件有稳定的可访问名称；取消勾选后仍可作为联合审核参考资料，不另加“仅作参考”说明。Standard / Deep 使用同一原生单选组，保持互斥与键盘操作。

## 对 PPAP 的具体视觉修改与后续建议

修正前的 [Workbench](../apps/web/src/Workbench.tsx) 文件行把文件名、来源徽标、识别提示、用途字段、规则字段、规则类型及说明并排叠加；旧 `Material` 四列顶部对齐。现改为单一上传入口、文件行 Checkbox、文件名、目标文件规则选择和移除动作；基础控件、色彩和间距由 shadcn/ui Nova 主题提供，领域布局集中在 [`src/ui/domain.tsx`](../apps/web/src/ui/domain.tsx)。

| 产品位置 | TSX 结构调整 | CSS 与视觉目标 |
| --- | --- | --- |
| 资料清单 | 一个上传入口；行内放审核对象选择、文件名、目标文件的规则选择和移除动作。默认不重复“沿用原件”“仅作参考”；新增/异常才强调。识别结果与必要错误保留，正常状态不占一段说明。 | 借鉴 Task Rows 的分隔行。主行纵向居中，标题、控件基线一致；长文件名不挤走操作。手机按两行重排，不复制画廊居中大框。 |
| 审核进度 | 一个真实阶段摘要；按需展开阶段活动，不展示模型内部推理。失败有原因和恢复动作。 | 借鉴 Thinking/Task Rows 的小状态图标、次级计数与单层展开；不使用定时器制造百分比或假步骤。 |
| 结果与复核 | 列表显示条款短标题与结论；选中后详情显示理由、证据、复核动作。完整规则放在有明确入口的详情中。 | 列表用细分隔线和轻选中底色，不每行套大卡片；标题、正文、次级信息最多三档视觉层级。 |
| 来源证据 | 借鉴 Context Cards：文件、位置、原文片段及真实跳转。多个来源同一层排列，去除重复角色说明。 | 摘录左对齐、正文可读；文件类型图标与来源名同排。紧凑不等于把正文缩到辅助标签字号。 |
| 筛选与动作 | 借鉴 Filter Table 的紧凑筛选和状态数量；预览、移除等惯用动作可图标化，保留可访问名称。业务模式和有歧义动作保留短文字。 | 淡底表达选择，强主色留给当前主要动作；必要选择控件不依赖 hover 才出现。 |

验收重点：正常文件行无注释式重复文案；角色选择不改变文件/规则版本绑定语义；参考资料仍能用于跨文件审核；错误和未覆盖条款不被隐藏；桌面、手机、平板的文字层级、对齐、焦点与触控目标一致。表中资料清单已部分实施，其他行仍是设计建议，不能视为浏览器验收结论。

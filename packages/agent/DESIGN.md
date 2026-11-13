# Deep：开发任务专用的审核 Checker

> 客户审核中的深度模式不写程序，改为复核 Suite 程序的结论（见 [DATA_MODEL](../../docs/DATA_MODEL.md) 深度模式一节）。本文的开发流程只用于平台离线开发检查程序（`scripts/deep_lab.py`），Pi 的开发要求在 `deep_agent/skills/ppap-semantic-audit/references/checker-development.md`。

本文定义 checker 路径的合同；具体行为以源码和测试为准。**Deep 根据本次 rules 和文件，开发、试跑并交付一组可独立执行的 checker。** 原 checker、资源类型和 Fluent API 是直接使用的实现基础。允许重构职责和接口，已有算法与有效行为继续保留。

本文拥有任务 checker 的接口、资源语义与开发流程；[DATA_MODEL](../../docs/DATA_MODEL.md) 拥有输入、确认与轮次的数据不变量，部署与授权沿用 [ARCHITECTURE 的执行边界](../../docs/ARCHITECTURE.md#执行)。当前实现是固定 checker，不是 RulePlan / AuditTrace。条款形式化的数据合同见 DATA_MODEL。实验命令见 [DEVELOPMENT](../../docs/DEVELOPMENT.md#本地-deep-checker-实验)；实现状态以源码和测试为准。

## 1. 任务接口

### 检查项与共享资源

输入沿用 `RuleInput`、`RuleItem`、`AuditInput`、`GroupContextInput`，结果沿用 `AuditResultItem`。生效规则包括已确认附录；已有代码提供实现经验，具体业务要求仍由本次输入决定。

现有 `RuleCheck` 承担单项执行职责。以下省略导入及执行回调：

```python
class RuleCheck:
    rule_item: RuleItem

    def check(self) -> AuditResultItem: ...
```

每个对象绑定具体条款、实现及文件资源上下文，仍可访问完整生效规则。当前有 N 个检查项，就装配 N 个独立 checker；每个条款 ID 恰好对应一个入口和一项结果。漏项、重复、多余绑定和返回 None 都须报告，NA 或 UNABLE 也保留对应条款身份。

数量指可执行检查项，不要求生成 N 个新类。多个检查项可以复用同一算法；一个条款中的多个义务由该项内部汇总。文件中的数据行数决定扫描范围，不决定 checker 数量。

将现有基类中的输入、文档读取、关联资料、缓存和关闭逻辑保留在任务运行容器中，本文简称 Audit。它暴露 `checks: tuple[RuleCheck, ...]`；原 engine 遍历这些对象：

```python
audit = create_audit(audit_file, rule, group_context)
try:
    results = [checker.check() for checker in audit.checks]
finally:
    if callable(close := getattr(audit, "close", None)):
        close()
```

Audit 共享工作簿、转换结果和只读解析缓存；每次 `check()` 独立收集断言、问题和证据。单项可以重跑，不能继承上次状态。独立入口不保证任意实现可并行，首条路径可顺序执行。

### V4 的复用入口

Deep 保留完整 `RuleInput`：`rule_profile`、`preset_key`、`customers/models/parts`、类型、正文和生效附录。从冻结轮次构建输入、guest manifest、开发提示、固定执行和恢复重放使用同一合同。输入摘要包含完整规则与整组文件身份；客户和预置来源也参与旧程序起点的选择，避免跨客户借用起点。

V4 通过现有 Resolver 选择客户及载体的 checker，再提供原源码、资源和源码条款绑定作为开发上下文。预置来源校验只确认客户、预置键、类型和源码摘要；当前条款可以修订。原预置条款的严格认证仍由 `validate_preset_body` 执行，Deep 的任务产物不继承该认证。条款 ID 相同不代表正文或附录未变，模型仍须逐条核对算法。

`GroupContextInput` 包含本轮全部冻结文件，包括其他目标文件。角色决定执行与产品处理，不限制关联读取；guest 只使用已校验摘要的冻结字节，并保留 `v4_source_type`、项目身份与关联规则。E 类来源保持其独立类型，T12 可以是目标或参考。规则客户列表表示适用范围，不能代替原件客户字段的观察。

蔚来、理想等 `ExcelAgent` 子类继续复用资源注入、`RuleContext` 和 Fluent API。UAES 原生子类保留 `NativeFacts`、字段/表头别名、关联核对和 `check(ith)`；任务方法无 `ctx` 参数，`bind_native_checks` 用原 `RuleCheck` 按显式条款 ID 调用该入口。它只绑定任务类自己声明的方法，条款增删、重排不会隐式执行继承的原条款。`check(ith)` 独立收集证据并记录本次规则求值是否完成；未知与技术错误不记为已完成。无来源的 PASS 继续降为 UNABLE。宿主在对象提供 `close()` 时调用它。

UAES 的电子 PDF 可沿用原生文字块读取；只有表格转换路径要求 `PdfSpec`。共享 V4 视觉读取经过 `AuditRuntime`，在 Deep 固定执行中缺少可重放观察时保持 UNABLE。已有 Standard 的模型能力继续由其运行时提供。

### 程序产物与开发入口

以下为现有接口的业务部分，省略宿主依赖和摘要等执行参数；附录由 `RuleInput.appendix` 携带：

```python
class CheckerArtifact(BaseModel):
    input_digest: str
    executor_revision: str
    entrypoint: str                  # 例如 "checker:create_audit"
    files: dict[str, bytes]           # checker.py 与可选的 resources.py


def adapt(
    audit_file: AuditInput,
    rule: RuleInput,
    group_context: GroupContextInput | None = None,
) -> CheckerArtifact: ...


def execute(
    artifact: CheckerArtifact,
    audit_file: AuditInput,
    rule: RuleInput,
    group_context: GroupContextInput | None = None,
) -> list[AuditResultItem]: ...
```

`adapt` 内可以多次运行候选程序；`execute` 执行固定产物。工厂返回重构后的现有基类实例，检查对象的装配与关闭由同一框架负责。

宿主根据生效规则、附录、文件版本和角色计算 `input_digest`，同时固定产物摘要与运行依赖版本。代码、资源、试跑摘要及澄清问题存入现有 attempt 产物存储；共享框架按版本引用。临时路径和会话记忆不能成为执行依赖。

当前保存的 observations 是试跑摘要和未回答的澄清问题，不是可重放的模型视觉回答。固定执行不调用模型；尚无可重放观察的视觉判断保留能力缺口。后续需要视觉能力时，应通过原 `AuditRuntime` 补充问题、来源、回答的固定与读取，不能把一次模型结论当作可重复执行的代码。新规则或新文件可以复用旧产物作为起点，但须重新核对适用性；任务程序不能只按规则 ID 缓存后直接用于其他文件。产物生成失败沿用执行失败合同，不能交付空的成功结果。

### 规则变化与复用边界

当前 `input_digest` 包含条款 ID、正文及分类等字段、规则身份与 profile、目标和关联文件的版本/内容/角色，以及 `RuleInput.appendix` 中的生效附录。同一条款 ID 的正文变化也会使旧产物失效。相同输入与执行版本可直接执行固定产物；变化后的输入只能把旧产物当作开发起点，先诊断，再适配并重新完整试跑。

规则变化不能仅按“是否还能运行”处理。阈值、量词、对象范围、例外、必需关系和空范围政策都可能变化；旧 checker 即使正常返回 PASS，也可能仍执行旧含义。开发上下文应从冻结的规则版本取得前后差异，逐项核对复用前提；不能只从旧代码反推旧规则。规则条款增加、删除或重排后，仍按本次条款身份重新装配 `checks`。

待接入的已确认形式解释、ontology 定义、值域及投影约定同样是生效依据。它们的内容和版本须进入已有规则 packet 与任务摘要；即使规则正文未改，也不能复用不同依据下的固定产物。布局绑定随文件版本和任务资源固定，不能作为跨文件隐含记忆。

**附录只有一个输入入口：`RuleInput.appendix`。** 产品、直接 adapt / execute 与 guest 载荷均使用它；不再接收独立的 `appendix=` 参数或传输顶层附录。现有 `scoped_rule` 按当前文件和条款筛选，提示、checker 和摘要使用同一份生效内容，原输入不被改写。空列表明确表示本次没有附录；修改或移除生效附录后，旧产物不能直接执行。范围外附录不进入本项判断或目标摘要。

## 2. 资源与 Fluent API

任务的 `resources.py` 直接实例化现有资源模型。原 `ExcelAgent` 已接收可选参数 `resource: RuleResource | None = None`：显式资源与原注册表资源进入同一个 `RuleContext` 和 resolver。多文件资源可用普通映射按冻结文件 ID、视图名称组织。

### Ontology projection 与 layout binding

资源定义承担两项工作：**投影定义文档怎样表达业务概念，布局绑定定义本次文件从哪里读取它。** 这是一条在现有资源上补足语义的路径，不另建资源引擎。

| 层次 | 决定什么 | 现有基础与待补内容 |
|---|---|---|
| Ontology | 实体、属性含义及类型、身份、关系和值域 | 待实现；从现有资源定义和用户确认的含义中提取，按实际消费者需要定义值域、作用范围与来源/版本 |
| Projection | 文档表示哪些概念；一条记录的粒度、成员判定、分组、属性归属和关系 | 复用 checker 的对象构建经验、`SectionModel` 及分组能力；待补明确的概念引用与投影约定 |
| Layout binding | sheet、区域、列、字段、合并格、PDF 页区及读取方式 | 直接使用 `RuleResource`、`SheetModel`、`SectionModel`、`ColumnModel`、`FieldModel`、`PdfSpec` 和原 resolver；缺少的绑定信息加在原类型中 |
| Check | 对上述对象执行本次规则的义务 | 继续使用 `RuleCheck.check() -> AuditResultItem` 和原 Fluent API，不把业务判定移到文档解析器 |

例如同一关系 `CharacteristicRecord.part -> Part` 可以有两种投影：每条记录重复零件编号/名称，或页眉声明一个零件并作用于指定记录区。前者绑定每行的列，后者绑定页眉字段与明确作用范围。规则若要求“每条记录能确定所属零件”，两种投影都可能满足；规则若明确要求“存在零件编号列”，页眉字段不能代替该列。属性可获得、物理列存在、属性值合法是三种不同判断。

先确定哪些对象属于审核范围，再检查其必填属性。缺少待检字段不能悄悄让对象退出范围。组标题可以保留为组，跨行对象保留全部成员位置；别名相同不证明实例相同。多个检查项需要同类对象时复用构建逻辑，保留冲突、排除依据和未确定成员。

### 概念、别名与合法值

Ontology 不只是字段别名表。零件编号和名称可以是同一 `Part` 的属性；编号身份可能需要客户或编号体系限定，名称可以有经确认的别名。合法值可以来自固定枚举，也可以来自指定范围、版本的主数据。以下为语义示意，尚非可调用 API：

```text
Part.identity = (numbering_system, number)
Part.number: PartNumber
Part.names: Set[PartName]           # 正式名称与已确认别名
CharacteristicRecord.part: Reference[Part]

ValidPartRef(n, t, scope, version) =
  exists p in Parts(scope, version): p.number = n and t in p.names
```

若 P001 的名称是“支架/安装支架”，P002 的名称是“壳体”，P001 与“壳体”的组合不能因为两个值分别出现过而通过。文件中观察到一个新值，只能产生候选，不能自动扩大合法值域；主数据范围不完整时，未命中不能直接证明非法。`filled_tokens` 表示原读取合同中的填写判定，不是合法值枚举。

`Present`、`ValidPartRef` 和 `HasColumn` 分开使用。规则只要求非空时，不能自动增加主数据一致性检查。用户可提出表达、实体及值域候选；初次落地复用有范围的附录与现有产物存储，不先建全局 ontology 服务。

### Excel 与 PDF 的同一开发路径

PDF 使用现有 `PdfSpec`、PPAP 预设和原转换实现。原生文字、预设自身标记及转换预览只提供候选配置，不证明配置正确，也不按文件名或文档类型自动确认。目标配置写入 `PDF_SPEC`，关联文件配置写入 `PDF_SPECS[file_version_id]`；通过 `load_related_pdf(file_version_id)` 读取，并绑定原文件身份。

PDF 配置与 checker 在同一个开发循环中修订。修改后重新转换、执行完整条款，再将配置与程序一起固定。保留页码、区域、原件映射及转换诊断；关联 PDF 的解析缓存共享，但本项使用的证据与缺口每次重置。

本轮验证优先 Excel，其次带文字层的电子版 PDF。固定 checker 通过原 `AuditRuntime` 读取原生内容，并明确关闭 OCR 回退；转换失败不能在正式执行时触发新的模型计算。扫描版不在本轮验收范围，没有可用文字层时逐条返回 UNABLE。原 OCR 与视觉实现继续保留给现有消费者，不因此删除；后续接入固定观察后再扩展 Deep 的能力范围。

Deep 生成代码直接调用原 Fluent API。例如，以下只展示已确认平面工序表的一项实现；真实产物须装配完整规则集合：

```python
class CurrentFlowAudit(ExcelAgent):
    @rule("工序名称与操作描述填写检查", clause_id="R4")
    def check_operations(self, ctx: RuleContext) -> tuple[str, str]:
        section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
        columns = section.columns("工序名称", "操作描述")
        checked = 0
        for row in section.rows_from(columns):
            if row.is_present("工序名称"):
                row.expect("操作描述", context="工序缺少操作描述")
                checked += 1
        if checked == 0:
            return "UNABLE", "未确定规则要求的工序范围"
        return "PASS", "已检查本次要求的工序"


def create_audit(
    audit_file: AuditInput,
    rule: RuleInput,
    group_context: GroupContextInput | None = None,
) -> BaseAgent:
    return CurrentFlowAudit(
        audit_file=audit_file,
        rule=rule,
        group_context=group_context,
        resource=RESOURCE,
    )
```

`resource` 注入、`clause_id` 和 `checks` 集合已由现有框架提供。原规则函数的 `tuple[str, str]` 可继续作为内部返回值；共享单项执行逻辑负责 GuardStop、异常、断言及证据汇总，最终返回 `AuditResultItem`。方法末尾的 PASS 不能覆盖已收集的 NG。

上例以工序名称识别成员的前提必须成立；若本条款还要求检查“工序名称缺失”，就必须用独立的记录判定识别工序，不能沿用该过滤条件。

规则声明按实际条款 ID 绑定方法，方法名和执行索引不代表业务身份。仅布局变化时可以直接使用原具体 checker；新规则则在同一框架内生成相应实现，数量不受原类固定方法数限制。原 `check(ith)` 在仍有调用方时转调已绑定对象的 `check()`，迁完后删除无消费者入口。

共享解析、匹配和对象处理继续使用原实现。层级工序、合并单元格、父行过滤、别名及归一化都有适用前提，须对照本次规则。任务可使用已确认坐标，但须绑定当前文件版本。

### 扩展由实际缺口触发

现有路径已接通规则绑定、资源注入、原结果收集和单项执行。诊断必须足以解释失败；以下扩展在真实任务证明现有接口不足时实施：

| 实际缺口 | 最小扩展 |
|---|---|
| 现有定位接口无法返回候选、搜索范围或停止原因 | 扩展原 resolver 与上下文诊断；需要公开探测时再增加方法 |
| 现有读取结果无法区分空白、确认缺失与未解决 | 复用并调整已有观察类型，按实际调用需要暴露带状态读取 |
| 原资源无法保存本次已确认的位置 | 在对应原资源模型中增加必要字段，复用同一解析器 |
| 现有结果无法携带必要的条款、来源或范围信息 | 扩展原结果与证据类型，并同步直接消费者 |

这些扩展共用既有类型与实现。缺少信息时保留诊断，不能在接口补齐前把它当作已知事实；无需预先建设全部字段和方法。

## 3. Deep 的开发循环

### 规则形式化与回译

条款形式化的数据合同见 [DATA_MODEL](../../docs/DATA_MODEL.md)。它不是 Checker 执行器。

已确认且完整的快照可以在用户明确交接时写入指定 Suite candidate 的 `rules.json` 顶层 `formalizations`。Optimizer 提示只读这份冻结快照。没有这份快照时，原规则路径不变。快照中的 `rejections` 是用户拒绝、没有可程序化判定标准的部分：提示要求不为它们写检查逻辑、不调用模型；`audit_runtime` 对整条拒绝不运行程序并给出 UNABLE，对部分拒绝把 PASS 改为 UNABLE，均附拒绝原因。


对用户规则尝试严格表达，保留原文及来源；形式表达是可核对的解释，不能冒充原文。每个条款至少明确以下内容：

| 内容 | 必须回答的问题 |
|---|---|
| 对象与适用性 | 检查哪个概念、粒度和集合？如何识别成员？何时不适用？ |
| 义务 | 要求列存在、值存在、值合法，还是对象间关系？ |
| 量词与条件 | 全部、至少一个、基数、比较、阈值、单位、例外分别是什么？ |
| 空与未知 | 确认空集合如何判定？范围不全、读不到或含义不明如何处理？ |
| 依据 | 使用哪个规则版本、已确认解释、ontology/值域范围和来源？ |

先检查 `audit.models` 已有的 `Predicate`、`ValueTest`、`ColumnExists`、量词及组合类型是否足以表达；适用的类型直接复用，缺失部分在原合同补足。它们用于约束和解释 checker，不恢复旧 RulePlan 执行路径，也不要求用一套通用 DSL 执行所有人工算法。不支持的语义谓词明确保留为未解决义务，不能用一个自由文本字段宣称已完成严格形式化。

回译从**同一份结构化解释**生成“本次会检查什么”，对支持的构造优先采用确定性渲染。checker 的开发输入、用户预览和固定产物必须引用这份解释；不能让模型分别写一份说明和一份含义不同的程序。用户编辑回译后，应更新解释并重新展示实质差异。实质歧义交由用户确认；含义清楚的规则可继续执行，不新增每一步都批准的流程。确认约束业务含义，不证明生成代码正确。

#### 例：列完整性与内容非空

以 [现有特殊特性 checker 的 `rule_2`](../audit/src/audit/checker_lib/nio/process_special_characteristic_agent.py) 为解释样例，不改变特殊特性清单在产品中的参考文件角色。设 R 是本次完整确定的数据范围。其现有成员过滤和空范围处理可写成：

```text
K = {零件编号, 零件名称}
V = {特性分类, 特性描述, 特性值, 特性类别, 特殊特性种类,
     控制方法（制造过程）, 防错方案描述, 频次, 样本数}
C = K ∪ V
Eligible(r) = Present(r, 零件编号) and Present(r, 零件名称)
Q = (and c in C: HasColumn(c))
    and (exists r in R: Eligible(r))
    and (forall r in R: Eligible(r) => (and c in V: Present(r, c)))
```

回译：“先要求所有指定列存在；只检查零件编号和名称均已填写的行，要求这些行的其余指定字段非空；没有这样的行则 NG。”这里缺少编号或名称的行被排除。这是可复用的人类经验中的适用假设，不能仅凭‘非空检查’标题推断用户同意它。

另一种可能意图是先独立识别特性记录，再检查所有必填字段：

```text
Q' = (and c in C: HasColumn(c))
     and (exists r in R: CharacteristicRecord(r))
     and (forall r in R: CharacteristicRecord(r) =>
           (and c in C: Present(r, c)))
```

回译：“每条已识别的特性记录都必须填写全部指定字段，包括零件编号和名称；没有特性记录则 NG。”一条编号缺失但可独立识别的特性记录，会区分两种解释。应确认用户要求，再选择或适配算法；两个公式都显式选择了非空范围政策，不能把它当成所有规则的默认值。

上述是行为解释与目标语义示例，不证明现有扫描已经实现完整的三值逻辑。谓词区分真、假、未知：`forall` 有确定反例可为假，只有范围完整且各项为真才为真；`exists` 有有效见证可为真，只有范围完整且无见证才为假。未读完不等于空集合。最终真/假/未知对应 PASS/NG/UNABLE；ERR 与明确的不适用另按第 4 节处理。

### 在现有环境中开发与修订

初始环境提供固定版本的框架、本次只读输入、相关 checker 与资源源码、用法和教学反例，以及可写任务目录。每轮按现有路由提供具体人工 checker 的源码、注册资源和按本次条款生成的起始类。起始类继承原 checker，以 `declared_rules()` 只装配本次声明的入口，同时保留父类方法及辅助函数；占位方法未实现时不能固定。共享能力按模块导入。验收保留样本不用于教学或调参，运行时只提供待审核规则和文件，不提供参考答案。

| 步骤 | 工作与反馈 |
|---|---|
| 理解与选择 | 从生效规则确定检查项及义务，选择可复用算法；接入形式化后，核对回译、歧义和规则变化 |
| 观察与实现 | 读取文件，适配资源、对象处理和每项检查代码；接入 ontology 后核对投影、绑定和值域依据 |
| 试跑与诊断 | 运行单项或受影响集合，查看异常、范围、原件位置及结果；用区分新旧规则的反例检查语义变化 |
| 修订 | 对照规则与文件修复实现；可以往返修改资源和算法 |
| 固定与执行 | 固定代码、资源和必要观察，执行全部生效检查项 |

复用现有读写文件、执行命令及运行反馈能力。旧产物先在新输入上诊断，再进入开发；每轮提供最近候选的完整源码，包括失败版本。局部试跑缩短反馈；资源或共享定义变化后重跑全部条款。完整试跑后，Agent 核对诊断并原样提交程序才固定；缺少确认不交付产物。开发结束可以是有依据的判定，也可以是明确的能力、输入或预算缺口；不以全部 PASS 为目标。

Agent 可以按原规则纠正实现。仍有合理歧义或需要改变业务标准时，进入既有澄清流程。优化可减少重复解析和视觉读取，不能跳过必要对象、改变规则或删除未知记录。

文档中新发现的表达、实例关系或值域成员可以产生推荐，在审核中或结束后提交给用户；未确认推荐不能进入生效依据。确认后沿用现有新轮次流程重新适配，不能原地修改已固定结果。不受该问题影响的检查继续执行。这里的开发试跑和技术修订属于当前输入的执行尝试；业务依据变化后的显式重审属于新的审核轮次。

## 4. 结果与执行边界

每项结果继续使用原结果类型，携带实际条款身份、判定、证据和必要诊断。完整性信息来自实际扫描和判断；无法可靠计数时保留未知。搜索未命中不等于确认不存在，反例展示上限也不是扫描上限。

| 情况 | 结果约束 |
|---|---|
| 规则要求“全部” | PASS 需要完整范围及全部必要判断 |
| 规则要求“存在一个” | 有效见证可以支持 PASS，不虚报全量扫描 |
| 已有明确反例 | 可以判 NG，同时保留其他未解问题 |
| 读取或解释不足、不适用、技术失败 | 分别使用有依据的 UNABLE、NA、ERR |
| 空集合、零断言、未执行或空结果 | 不自动 PASS；规则明确规定空范围处理时保留其依据 |

观察须保留原值，区分已知值、已定位空白、足够范围内确认缺失和未解决；零与 False 仍是已知值。原生读取与模型解释分开。已有信息不足以作出判断时，使用原诊断通路报告，不能靠包装结果补造证据。

生成代码只在现有受控 guest 中运行。宿主控制输入、框架、预算、超时和输出；文档与模型访问通过原运行时及网关。正式结果必须来自固定产物的实际执行，不能以 Agent 手写 JSON 代替。

宿主核对输入及产物身份、条款完整性、来源与运行记录。**程序可执行、可重放不证明语义正确。** `host_source_trace_v1` 不能表示 Python checker 已验证；产品映射的 `evidence_verified` 为 false。

产品确认、授权、输入版本、轮次与尝试、人工复核及恢复操作继续遵守开头引用的合同。Deep 只将实际诊断和待确认问题接入现有通路。

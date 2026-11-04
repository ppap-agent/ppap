# Deep：开发任务专用的审核 Checker

本文定义 checker 路径的合同；具体行为以源码和测试为准。**Deep 根据本次 rules 和文件，开发、试跑并交付一组可独立执行的 checker。** 原 checker、资源类型和 Fluent API 是直接使用的实现基础。允许重构职责和接口，已有算法与有效行为继续保留。

本文拥有任务 checker 的接口、资源语义与开发流程；[PRODUCT](PRODUCT.md) 拥有用户确认旅程，[DATA_MODEL](../../docs/DATA_MODEL.md) 拥有输入、确认与轮次的数据不变量，部署与授权沿用 [ARCHITECTURE 的执行边界](../../docs/ARCHITECTURE.md#执行)。当前实现是固定 checker，不是 RulePlan / AuditTrace。下文的形式化回译、ontology 投影和值域约束是待实现的扩展；第 6 节区分现有能力与验证缺口。

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

Deep 保留完整 `RuleInput`：`rule_profile`、`preset_key`、`preset_source_sha256`、`customers/models/parts`、类型、正文和生效附录。从冻结轮次构建输入、guest manifest、开发提示、固定执行和恢复重放使用同一合同。输入摘要包含完整规则与整组文件身份；客户和预置来源也参与旧程序起点的选择，避免跨客户借用起点。

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
| Ontology | 实体、属性含义及类型、身份、关系和值域 | `audit.models` 已有 `EntityConcept`、`RelationshipConcept`、`Ontology`；当前 checker 路径未消费它们。复用适用定义，按需要补值域、作用范围与来源/版本 |
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

`Present`、`ValidPartRef` 和 `HasColumn` 分开使用。规则只要求非空时，不能自动增加主数据一致性检查。用户可提出表达、实体及值域候选；确认、修改或移除的产品行为见 [PRODUCT 的确认旅程](PRODUCT.md#2-未来用户旅程)。初次落地复用有范围的附录与现有产物存储，不先建全局 ontology 服务。

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

### 规则形式化与回译：待实现

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

以 [现有特殊特性 checker 的 `rule_2`](../audit/src/audit/checkers/v3/process_special_characteristic_agent.py) 为解释样例，不改变特殊特性清单在产品中的参考文件角色。设 R 是本次完整确定的数据范围。其现有成员过滤和空范围处理可写成：

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

## 5. 复用、调整与删除

此表是改动位置的唯一清单；接口和行为要求由前文定义。

| 现有位置 | 处理方式 |
|---|---|
| [审核合同](../audit/src/audit/contracts.py) | 复用输入、条款、结果、证据及附录字段；已统一为 `RuleInput.appendix`，其他字段按实际缺口扩展 |
| [现有 checkers](../audit/src/audit/checkers) 与 [engine](../audit/src/audit/engine/engine.py) | 已复用算法、单项执行和共享文档生命周期；继续提取适用前提、扫描经验与反例，不按 legacy 标签删除有效实现 |
| [声明发现](../audit/src/audit/engine/checkers/rule_declarations.py) 与 [规则覆盖](../audit/src/audit/rule_coverage.py) | Deep 已按条款绑定并动态装配；Standard 的固定顺序合同保持。索引兼容入口仅在消费者迁完后删除 |
| [资源模型](../audit/src/audit/engine/rule_engine/contracts.py) 与 [Fluent 实现](../audit/src/audit/engine/rule_engine/fluent.py) | 直接复用资源、解析、扫描及断言；在原上下文增加实际执行记录，按需补诊断 |
| [文档能力](../documents/src/documents) 与 [PDF 配置](../documents/src/documents/pdf/spec.py) | 复用格式处理、预设和提取结果，接收任务配置并保留来源映射 |
| [证据读取](../audit/src/audit/evidence.py)、[定位与候选](../audit/src/audit/models.py)、[局部观察](../audit/src/audit/checker_tools.py) | 保留 snapshot、定位和窄字段观察。旧 Hole 模块已无消费者，不再保留。 |
| `audit.models` 中已有语义类型 | 核对并复用适用的概念、关系及谓词类型；待补形式解释、回译和值域/投影约束。类型存在不等于已接入 checker；不恢复无消费者的计划执行体系 |
| [AuditRuntime](../audit/src/audit/engine/runtime.py) 与 [执行环境](src/agent/execution) | 复用能力注入、隔离、网关、预算和反馈；调整 guest 的开发与执行载荷 |
| [auditor](src/agent/auditor.py)、[协议](src/agent/protocol.py) 与 [skill](src/agent/skill) | 已编排 adapt / execute、保存固定程序并提供原框架开发指南；后续在同一路径接入形式解释与语义资源 |
| 现有 product 结果及反馈映射 | 复用附录、候选、确认回执和新轮次；待补同源回译、语义推荐及版本输入，不新增确认账本 |
| 旧 Deep 的 RulePlan / AuditTrace 主执行路径 | 已被固定 checker 路径替代；不恢复第二套求值器 |
| 通用单条 checker 模板、独立且提前冻结的 PDF layout 阶段 | 删除重复入口；由当前条款生成起始类，PDF 资源随同一 checker 循环适配 |

删除按实际职责和消费者判断，不按整个文件或目录判断。历史产物继续可读；旧尝试按固定版本完成或明确失败。历史可读性不要求永久运行两套审核核心。

## 6. 实现状态与验证

本节说明代码能力和证据边界；工作进展仍由 Issue / PR 管理。待实现项不能由现有接口测试推定为已经完成。

| 能力 | 当前状态 | 仍需实现或验证 |
|---|---|---|
| 任务专用 checker、动态条款数量、资源注入与原 Fluent API | 已实现，受控测试覆盖绑定、完整性、诊断、修订及固定执行 | 更广规则与文件的语义正确性需分别验证 |
| V4 规则输入、客户 Resolver、整组上下文与 UAES 逐条调用 | 已实现；受控 runner 与合成 Excel/PDF 文件覆盖多目标关联、条款增删与重排、资源适配、证据隔离、guest 执行和重放 | 尚未用真实模型验证 V4 多客户开发成功率；尚未完成容器与产品端到端验收，视觉观察固定仍待实现 |
| 规则内容变化使旧产物失效 | 已实现正文及生效附录等输入摘要；附录统一入口，修改/移除后的旧产物拒绝执行，可作新适配起点 | 明确提供新旧规则差异；扩大规则语义的反例覆盖 |
| Excel 诊断、布局适配与重放 | 已有真实模型的原表和移位表验证 | 局部样本不证明任意布局或新规则成功率 |
| 电子版 PDF 配置、执行与重放 | 已有真实模型实验；结果未用独立人工预期评分 | 审核准确性未据此验收；扫描版仍不在本轮范围 |
| 严格形式解释及同源回译 | 待实现；已有部分谓词/组合类型可复用 | 歧义候选、用户预览、程序遵循解释及反例验证 |
| Ontology projection / layout binding | 已有概念类型、资源模型和有效人工算法；尚未连成此语义合同 | 语义引用、身份/值域及其范围/版本、确认后的输入固定 |
| 用户确认与新轮次 | 已有附录保存/移除、作用范围、回执及保存并分析合同 | 新语义候选与形式化接入后的 checker → 产品 → 新 checker 完整旅程验证 |

前端与 MCP 消费经过 `checker_response` / `product_result` 映射的结果、覆盖和澄清问题；`CheckerArtifact` 的程序文件与试跑记录仍是内部执行产物，没有面向用户的程序查看接口。现有前端读取冻结轮次和同一反馈服务，普通 `/mcp` 可读取结果；Deep 的规则确认与显式重审目前另由 `/mcp/deep` 提供。单次 MCP 配置下发现并使用完整 Deep 能力尚未统一。以上结果合同测试不等于浏览器、真实模型和 MCP 客户端的完整联调。

规则变化通过 `--source fixed.xlsx --rules rule-input.json --expected expected.json --prior artifact.json` 实测。`--rules` 接收完整 `RuleInput`，冻结本次规则，并取消原样本预期；`--expected` 是宿主独立评分输入，不提供给模型。适配与异地重放均读取冻结规则。预期必须先独立确定，不能从生成代码推导；新旧代码都能运行不是验收标准。命令见 [DEVELOPMENT](../../docs/DEVELOPMENT.md)。

2026-11-03（UTC）的真实模型实验使用 Grok 4.7（high，开发阶段最多 128 轮），固定 Excel 的“测量记录”页：两条记录的“偏差值”为 0.05、0.15，“审核偏差”为 0.30、0.40。目标及三份参考文件的字节保持不变。每次变化从旧 checker 诊断、适配，再固定执行；独立预期只供宿主评分。已验证的规则变化如下：

| 变化 | 独立预期与实测结果 |
|---|---|
| 基线：全部偏差值 ≤ 0.2 | R1 PASS |
| 阈值改为 0.1 | R1 NG，指出第 3 行的 0.15 |
| 列名改写为“偏差数值（对应表头‘偏差值’）” | R1 PASS，仍读取原列 |
| 明确改读“审核偏差”列，阈值保持 0.2 | R1 NG，指出两行的 0.30、0.40 |
| 从阈值 0.1 的“全部”改为“至少一条” | R1 PASS，以第 2 行的 0.05 为满足条件的证据 |
| 一条增为三条并重排为 R2、R1、R3 | NG、PASS、PASS；R3 检查数据行数等于 2，无缺项或多余项 |
| 从三条删回 R1 | 仅 R1 PASS，无残留 R2/R3 |

以上七个场景均通过无模型凭据的新进程重放，完整结果一致。生成程序复用 `ExcelAgent`、`RuleResource` 与原 `Table` Fluent API。此证据仅覆盖一个简单、含明确列绑定的合成 Excel；不证明模糊列名、复杂规则或任意文件的成功率。单次开发仍需数分钟，尚未验证生产延迟与成本。

2026-11-03 的第二组实验使用真实 Blanc Spoiler 特殊特性清单及原三份参考文件，保持文件字节和条款身份 R1 不变。原件第 4、5 行都是“产品”特性：前者控制方法为预防性防错、频次 NA；后者为年度试验、频次每年1次。Grok 4.7 从基线 checker 适配各变体，继续复用 `ProcessSpecialCharacteristicAgent`、资源定义和 Fluent API。规则及独立预期见 [实验输入](../../tests/fixtures/deep_lab/real_rule_changes/)，命令见 DEVELOPMENT。

| 本次条款要求 | 独立预期 | 真实模型验证 |
|---|---|---|
| 全部对象频次为每年1次 | NG | 通过；指出第 4 行 NA |
| 只检查年度试验对象的频次 | PASS | 通过；实际纳入 1 条 |
| 全部对象，但预防性防错豁免频次 | PASS | 通过；识别 2 条并豁免 1 条 |
| 仅检查“过程”分类，空范围无法判断 | UNABLE | 通过；确认两条均非过程对象 |
| 必须至少有一条“过程”对象 | NG | 未完成；模型服务 HTTP 402 中断 |
| 从独立检测报告判断合格，缺报告无法判断 | UNABLE | 未完成；模型服务 HTTP 402 中断 |
| 明确要求提交独立检测报告 | NG | 未完成；模型服务 HTTP 402 中断 |

已完成四项均核对目标与参考文件摘要，并通过无模型凭据的完整结果重放。后三项不是语义错误或通过证据；服务返回额度耗尽，候选没有固定为正式产物。实验 runner 现在保留候选、将服务 HTTP 错误单独报告并停止，不把它当作可由 checker 修订解决的问题；此改动不表示产品容器 runner 的故障恢复已验收。

仍需完成空集合必需项及缺参考资料的真实模型验证，并扩大到更大对象集合、嵌套例外、模糊条件、损坏或错误版本参考文件。四项通过不等于整个真实样本的原始业务审核通过，也不证明生产成本与延迟达标。

通过 `scripts/deep_lab.py --recipe checker` 验证真实模型开发；通过 `--prior artifact.json --variant shifted-table` 对保留标题、将表格和合并区域下移的副本进行诊断与适配。实验保存程序、完整执行结果及评分，并在无模型凭据的新进程中复制输入、重载产物和重放，比较完整结果。模型没有参考状态；人工预期只用于运行后的评分。实验运行器与产品共用候选读取、开发循环及固定执行，仍不代替容器隔离和产品端到端验收。

保留以下验收证据：

- 任意有效规则集合与检查对象一一对应，漏项和重复失败；每项可独立重跑，共享资源而不共享上次断言状态。
- 原 checker 与资源回归不退化；新布局的证据指向新文件，跨文件、层级和重复对象不被错误过滤。
- PDF 保留原件位置与转换缺口；定位失败、读失败、预算用尽和未执行项不成为 PASS。
- 固定产物与其输入、框架版本及执行结果一致，确认后的新输入走原产品流程。
- 形式表达、回译与 checker 对同一反例保持一致；不能因必填属性缺失而漏掉已识别对象。
- 逐行列与页眉继承在规则允许时表达同一对象关系；明确要求列存在时不能互相替代。
- 概念映射、实例关系和值域确认分别生效；新增、替换或移除确认后，旧结果保留、新依据重新计算，原件冲突仍可导致 NG。

[规则合同回归](../audit/tests/test_v3_rule_contracts.py) 和 [稀疏扫描回归](../audit/tests/test_sparse_sheet_scan.py) 保护原实现；[checker 程序测试](tests/test_checker_program.py) 验证当前开发与执行接口，包括宿主/guest 与直接 API 的附录传递、范围筛选、修改和移除后的产物失效。确认保存与轮次合同见 [反馈集成测试](../../tests/integration/product/test_deep_feedback_contract.py)，它不代替新语义的完整产品旅程。完整范围按 [测试策略](../../docs/TESTING.md) 执行。

受控 runner 测试证明协议和失败处理，真实模型任务验证诊断及适配能力，两者证据分开。评估逐项正确性、漏检、误报、证据与范围、未解决问题、成本和迭代次数；测试数量或 PASS 比例不能单独证明审核成功。

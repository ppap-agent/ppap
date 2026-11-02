# Deep：开发任务专用的审核 Checker

本文定义目标设计；当前行为以源码和测试为准。**Deep 根据本次 rules 和文件，开发、试跑并交付一组可独立执行的 checker。** 原 checker、资源类型和 Fluent API 是直接使用的实现基础。允许重构职责和接口，已有算法与有效行为继续保留。

产品语义沿用 [PRODUCT 的产品约束](PRODUCT.md#8-必须保持的产品事实)，部署与授权沿用 [ARCHITECTURE 的执行边界](../../docs/ARCHITECTURE.md#执行)。本文拥有审核核心目标。当前实现是固定 checker，不是 RulePlan / AuditTrace。

## 1. 任务接口

### 检查项与共享资源

输入沿用 `RuleInput`、`RuleItem`、`AuditInput`、`GroupContextInput`，结果沿用 `AuditResultItem`。生效规则包括已确认附录；已有代码提供实现经验，具体业务要求仍由本次输入决定。

从当前规则绑定及单项执行机制中提取以下职责。代码片段是目标接口，省略导入：

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
    audit.close()
```

Audit 共享工作簿、转换结果和只读解析缓存；每次 `check()` 独立收集断言、问题和证据。单项可以重跑，不能继承上次状态。独立入口不保证任意实现可并行，首条路径可顺序执行。

### 程序产物与开发入口

```python
class CheckerArtifact(BaseModel):
    input_digest: str
    executor_revision: str
    entrypoint: str                  # 例如 "checker:create_audit"
    files: dict[str, bytes]           # 任务代码、资源及必要的辅助数据


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

宿主根据生效规则、附录、文件版本和角色计算 `input_digest`，同时固定产物摘要与运行依赖版本。代码、资源及必要观察存入现有 attempt 产物存储；共享框架按版本引用。临时路径和会话记忆不能成为执行依赖。

模型观察保存实际问题、来源和回答；重算观察属于新的计算。新规则或新文件可以复用旧产物作为起点，但须重新核对适用性；任务程序不能只按规则 ID 缓存后直接用于其他文件。产物生成失败沿用执行失败合同，不能交付空的成功结果。

## 2. 资源与 Fluent API

任务的 `resources.py` 直接实例化现有资源模型。为原 `ExcelAgent` 增加可选参数 `resource: RuleResource | None = None`：显式资源与原注册表资源进入同一个 `RuleContext` 和 resolver。多文件资源可用普通映射按冻结文件 ID、视图名称组织。

PDF 使用现有 `PdfSpec` 和 PPAP 预设，由原运行时接收任务配置。保留提取结果中的页码、区域、原件映射、回退与转换诊断。转换表格使用同一 Fluent API；图形、签章和连线等继续使用已有原生或视觉读取。其他已支持格式继续复用。

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

`resource` 注入、`clause_id` 和 `checks` 集合是拟调整接口。原规则函数的 `tuple[str, str]` 可继续作为内部返回值；共享单项执行逻辑负责 GuardStop、异常、断言及证据汇总，最终返回 `AuditResultItem`。方法末尾的 PASS 不能覆盖已收集的 NG。

规则声明按实际条款 ID 绑定方法，方法名和执行索引不代表业务身份。仅布局变化时可以直接使用原具体 checker；新规则则在同一框架内生成相应实现，数量不受原类固定方法数限制。原 `check(ith)` 在仍有调用方时转调已绑定对象的 `check()`，迁完后删除无消费者入口。

共享解析、匹配和对象处理继续使用原实现。层级工序、合并单元格、父行过滤、别名及归一化都有适用前提，须对照本次规则；多个检查项需要同类对象时共享构建逻辑，并保留成员位置、冲突和排除依据。任务可使用已确认坐标，但须绑定当前文件版本。

### 扩展由实际缺口触发

首条路径先接通规则绑定、资源注入、原结果收集和单项执行。诊断必须足以解释失败；以下扩展在真实任务证明现有接口不足时实施：

| 实际缺口 | 最小扩展 |
|---|---|
| 现有定位接口无法返回候选、搜索范围或停止原因 | 扩展原 resolver 与上下文诊断；需要公开探测时再增加方法 |
| 现有读取结果无法区分空白、确认缺失与未解决 | 复用并调整已有观察类型，按实际调用需要暴露带状态读取 |
| 原资源无法保存本次已确认的位置 | 在对应原资源模型中增加必要字段，复用同一解析器 |
| 现有结果无法携带必要的条款、来源或范围信息 | 扩展原结果与证据类型，并同步直接消费者 |

这些扩展共用既有类型与实现。缺少信息时保留诊断，不能在接口补齐前把它当作已知事实；无需预先建设全部字段和方法。

## 3. Deep 的开发循环

初始环境提供固定版本的框架、本次只读输入、相关 checker 与资源源码、用法和教学反例，以及可写任务目录。模板是可运行的原代码；共享能力按模块导入，其余示例按需读取。验收保留样本不用于教学或调参，运行时只提供待审核规则和文件，不提供参考答案。

| 步骤 | 工作与反馈 |
|---|---|
| 理解与选择 | 从生效规则确定检查项及义务，选择可复用算法 |
| 观察与实现 | 读取文件，适配资源、对象处理和每项检查代码 |
| 试跑与诊断 | 运行单项或受影响集合，查看异常、范围、原件位置及结果 |
| 修订 | 对照规则与文件修复实现；可以往返修改资源和算法 |
| 固定与执行 | 固定代码、资源和必要观察，执行全部生效检查项 |

复用现有读写文件、执行命令及运行反馈能力。局部试跑缩短反馈，资源或共享函数变化后重跑受影响项，正式保存前执行完整集合。开发结束可以是有依据的判定，也可以是明确的能力、输入或预算缺口；不以全部 PASS 为目标。

Agent 可以按原规则纠正实现。仍有合理歧义或需要改变业务标准时，进入既有澄清流程。优化可减少重复解析和视觉读取，不能跳过必要对象、改变规则或删除未知记录。

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
| [审核合同](../audit/src/audit/contracts.py) | 复用输入、条款、结果和证据类型；补足生效附录及必要身份，其他字段按实际缺口扩展 |
| [现有 checkers](../audit/src/audit/checkers) 与 [engine](../audit/src/audit/engine/engine.py) | 直接使用算法；从规则绑定、执行和 finalizer 中提取单项职责，保留共享文档生命周期 |
| [声明发现](../audit/src/audit/engine/checkers/rule_declarations.py) 与 [规则覆盖](../audit/src/audit/rule_coverage.py) | 改为明确条款绑定，迁移后移除无消费者的索引与固定编号限制 |
| [资源模型](../audit/src/audit/engine/rule_engine/contracts.py) 与 [Fluent 实现](../audit/src/audit/engine/rule_engine/fluent.py) | 直接复用资源、解析、扫描及断言；在原上下文增加实际执行记录，按需补诊断 |
| [文档能力](../documents/src/documents) 与 [PDF 配置](../documents/src/documents/pdf/spec.py) | 复用格式处理、预设和提取结果，接收任务配置并保留来源映射 |
| [证据读取](../audit/src/audit/evidence.py)、[定位与候选](../audit/src/audit/models.py)、[局部观察](../audit/src/audit/checker_tools.py) | 保留 snapshot、定位和窄字段观察。旧 Hole 模块已无消费者，不再保留。 |
| [AuditRuntime](../audit/src/audit/engine/runtime.py) 与 [执行环境](src/agent/execution) | 复用能力注入、隔离、网关、预算和反馈；调整 guest 的开发与执行载荷 |
| [auditor](src/agent/auditor.py)、[协议](src/agent/protocol.py) 与 [skill](src/agent/skill) | 编排 adapt / execute，保存固定程序，将操作指南改为原框架上的 checker 开发指南 |
| 现有 product 结果及反馈映射 | 接入执行结果、问题和验证含义；沿用原业务存储与操作 |
| 旧 Deep 的强制规则编译、RulePlan / AuditTrace 求值、专用工具、缓存及阶段测试 | 替代验收且消费者迁完后删除；其中可复用的读取、算法、类型、校验与业务反例先迁出 |

删除按实际职责和消费者判断，不按整个文件或目录判断。历史产物继续可读；旧尝试按固定版本完成或明确失败。历史可读性不要求永久运行两套审核核心。

## 6. 实施与验证

先从一个现有 checker 提取单项执行，用原回归验证行为；再让真实 Deep 适配新布局，验证开发、试跑、修订、固定与执行。随后覆盖检查项数量和内容不同的规则、关联文件及 PDF，接通产品结果后再清理旧主路径。

保留以下验收证据：

- 任意有效规则集合与检查对象一一对应，漏项和重复失败；每项可独立重跑，共享资源而不共享上次断言状态。
- 原 checker 与资源回归不退化；新布局的证据指向新文件，跨文件、层级和重复对象不被错误过滤。
- PDF 保留原件位置与转换缺口；定位失败、读失败、预算用尽和未执行项不成为 PASS。
- 固定产物与其输入、框架版本及执行结果一致，确认后的新输入走原产品流程。

[规则合同回归](../audit/tests/test_v3_rule_contracts.py) 和 [稀疏扫描回归](../audit/tests/test_sparse_sheet_scan.py) 保护原实现；新增单项执行与绑定测试验证目标接口。完整范围按 [测试策略](../../docs/TESTING.md) 执行。

受控 runner 测试证明协议和失败处理，真实模型任务验证诊断及适配能力，两者证据分开。评估逐项正确性、漏检、误报、证据与范围、未解决问题、成本和迭代次数；测试数量或 PASS 比例不能单独证明审核成功。

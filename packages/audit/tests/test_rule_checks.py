from pathlib import Path
from types import MappingProxyType

from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.checker_lib.nio.process_flow_chart_excel_agent import ProcessFlowChartExcelAgent
from audit.contracts import AuditInput, RuleInput, RuleItem
from audit.engine.engine import _execute_agent, run_audit
from audit.engine.rule_engine.context import RuleContext
from audit.engine.rule_engine.contracts import DomainTokensModel, RuleResource, SheetModel
from audit.engine.rule_engine.fluent import Table
from audit.kinds import PpapDocumentType
from openpyxl import Workbook


def saved_workbook(tmp_path: Path) -> str:
    path = tmp_path / "target.xlsx"
    workbook = Workbook()
    workbook.save(path)
    workbook.close()
    return str(path)


def rule_input(*items: tuple[str, str]) -> RuleInput:
    return RuleInput(
        rule_id="confirmed-clauses",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[{"id": clause_id, "category": category, "rule_to_check": category} for clause_id, category in items],
    )


class SampleAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("第一项", "第二项")

    @rule("第一项", clause_id="A")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        return "NA", "第一项不适用"

    @rule("第二项", clause_id="B")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        ctx.record_ng("已收集的不合格")
        return "PASS", "方法末尾通过"


class ProbeAudit(SampleAudit):
    def __init__(self, audit_file: AuditInput, rule: RuleInput) -> None:
        self.starts: list[list[str]] = []
        super().__init__(audit_file, rule)

    @rule("第一项", clause_id="A")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        self.starts.append(list(ctx.collected_ng_messages))
        ctx.record_ng("only-this-run")
        return "PASS", "probe"


class SummaryAudit(SampleAudit):
    @rule("第二项", clause_id="B")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        ctx.record_ng("已收集的不合格")
        return "NG", ctx.build_ng_summary()


class DropAudit(SampleAudit):
    def _execute_rule_check(self, rule_no: int):
        if rule_no == 1:
            return None
        return super()._execute_rule_check(rule_no)


class QuestionAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("表头", "比较")

    @rule("表头", clause_id="A")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        ctx.note_cell("Sheet", 1, 1)
        ctx.ask(
            "header-meaning",
            "表头按什么理解？",
            (("left", "左列", "按左列表头理解"), ("right", "右列", "按右列表头理解")),
        )
        return "PASS", "未确认"

    @rule("比较", clause_id="B")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        ctx.record_ng("已比较")
        return "NG", "已比较"


class DuplicateBindingAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("第一项", "重复项")
    calls: list[str]

    def __init__(self, audit_file: AuditInput, rule: RuleInput) -> None:
        self.calls = []
        super().__init__(audit_file, rule)

    @rule("第一项", clause_id="A")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        self.calls.append("rule_1")
        return "PASS", "不应执行"

    @rule("重复项", clause_id="A")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        self.calls.append("rule_2")
        return "PASS", "不应执行"


def test_effective_clauses_bind_by_id_and_keep_identity(tmp_path: Path) -> None:
    agent = SampleAudit(AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)), rule_input(("B", "第二项"), ("A", "第一项")))
    try:
        assert [check.rule_item.id for check in agent.checks or ()] == ["B", "A"]
        assert agent.check(0) is not None
        assert agent.check(0).clause_id == "A"
        assert agent.check(0).status == "NA"
        results = [check.check() for check in agent.checks or ()]
    finally:
        agent.close()

    assert [(result.clause_id, result.status) for result in results] == [("B", "NG"), ("A", "NA")]
    assert "已收集的不合格" in results[0].suggestions
    assert "方法末尾通过" not in results[0].suggestions
    assert results[1].status == "NA"


def test_engine_reports_binding_gaps_and_none_without_dropping_them(tmp_path: Path) -> None:
    missing = _execute_agent(
        SampleAudit,
        AuditInput(audit_id="target", file_url=str(tmp_path / "missing.xlsx")),
        rule_input(("Z", "不存在的条款")),
        None,
        None,
    )
    assert [result.clause_id for result in missing] == ["Z", "A", "B"]
    assert missing[0].status == "ERR"
    assert "没有对应的检查入口" in missing[0].suggestions
    assert all(result.status == "ERR" and "不在本次生效规则中" in result.suggestions for result in missing[1:])

    duplicate = _execute_agent(
        SampleAudit,
        AuditInput(audit_id="target", file_url=str(tmp_path / "duplicate.xlsx")),
        rule_input(("A", "第一项"), ("A", "第一项")),
        None,
        None,
    )
    assert [result.clause_id for result in duplicate] == ["A", "A", "B"]
    assert all("在生效规则中重复" in result.suggestions for result in duplicate[:2])
    assert duplicate[2].clause_id == "B"

    agent = DuplicateBindingAudit(AuditInput(audit_id="target", file_url=str(tmp_path / "bound-twice.xlsx")), rule_input(("A", "第一项")))
    try:
        bound = agent.check(0)
        assert bound is not None
        assert bound.status == "ERR"
        assert bound.clause_id == "A"
        assert "绑定了多个检查入口" in bound.suggestions
        assert agent.calls == []
    finally:
        agent.close()

    results = _execute_agent(
        DropAudit,
        AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)),
        rule_input(("A", "第一项"), ("B", "第二项")),
        None,
        None,
    )
    assert [(result.clause_id, result.status) for result in results] == [("A", "ERR"), ("B", "NG")]
    assert "没有返回结果" in results[0].suggestions


class _FaultItem:
    def __init__(self, clause_id: str) -> None:
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"


class _BoomCheck:
    def __init__(self) -> None:
        self.rule_item = _FaultItem("A")

    def check(self):
        raise RuntimeError("读失败")


class _KeptCheck:
    def __init__(self) -> None:
        self.rule_item = _FaultItem("B")

    def check(self):
        from audit.contracts import AuditResultItem

        return AuditResultItem(classification="规则审核", item="B", priority="P1", status="NG", suggestions="已比较", clause_id="B")


class FaultAudit:
    def __init__(self, audit_file: AuditInput, rule: RuleInput, group_context=None) -> None:
        del audit_file, rule, group_context
        self.checks = (_BoomCheck(), _KeptCheck())

    def close(self) -> None:
        return None


def test_one_clause_fault_does_not_drop_the_other_clause(tmp_path: Path) -> None:
    results = _execute_agent(
        FaultAudit,
        AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)),
        rule_input(("A", "第一项"), ("B", "第二项")),
        None,
        None,
    )

    assert [(item.clause_id, item.status) for item in results] == [("A", "ERR"), ("B", "NG")]
    assert "技术失败" in results[0].suggestions
    assert "读失败" in results[0].suggestions
    assert "已比较" in results[1].suggestions
    assert "Audit engine execution failed" not in results[0].suggestions


def test_an_open_question_does_not_pass_and_keeps_the_other_clause(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "频次"
    workbook.save(path)
    workbook.close()
    agent = QuestionAudit(AuditInput(audit_id="target", file_url=str(path), file_metadata={"file_version_id": "version-1"}), rule_input(("A", "表头"), ("B", "比较")))
    try:
        first = [check.check() for check in agent.checks or ()]
        second = [check.check() for check in agent.checks or ()]
    finally:
        agent.close()

    assert [(item.clause_id, item.status) for item in first] == [("A", "UNABLE"), ("B", "NG")]
    assert "表头按什么理解" in first[0].suggestions
    assert "不能判为通过" in first[0].suggestions
    assert "未确认" not in first[0].suggestions
    question = first[0].clarifications[0]
    assert question["id"] == "header-meaning"
    assert question["clause_id"] == "A"
    assert question["sources"][0]["locator"] == {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"}
    assert question["sources"][0]["excerpt"] == "频次"
    assert [item["id"] for item in question["candidates"]] == ["left", "right"]
    assert first[1].suggestions == "已比较" or "已比较" in first[1].suggestions
    assert first[1].clarifications == []
    assert len(second[0].clarifications) == 1


def test_rerun_shares_the_workbook_but_not_the_previous_assertions(tmp_path: Path) -> None:
    path = saved_workbook(tmp_path)
    agent = ProbeAudit(AuditInput(audit_id="target", file_url=path), rule_input(("A", "第一项"), ("B", "第二项")))
    try:
        first = agent.check(1)
        assert first is not None
        second = agent.check(1)
        assert second is not None
        assert first.suggestions == second.suggestions
        probed = agent.check(0)
        assert probed is not None
        assert agent.starts == [[]]
        assert "已收集的不合格" not in probed.suggestions
        cached = agent._workbook_cache
        import audit.checker_lib.nio.excel_agent as excel_agent_module

        original_loader = excel_agent_module.load_excel_workbook

        def fail_reload(*_args, **_kwargs):
            raise AssertionError("workbook reloaded")

        excel_agent_module.load_excel_workbook = fail_reload
        try:
            again = agent.check(0)
        finally:
            excel_agent_module.load_excel_workbook = original_loader
        assert again is not None
        assert agent._workbook_cache is cached
    finally:
        agent.close()


def test_checks_share_the_parse_cache_but_not_the_previous_assertions(tmp_path: Path) -> None:
    from audit.engine.rule_engine import resolver

    class SharedAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True
        EXPECTED_RULE_CATEGORIES = ("第一项", "第二项")

        @rule("第一项", clause_id="A")
        def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_ng("只属于第一项")
            section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
            columns = section.columns("工序名称")
            names = [str(row.value("工序名称")) for row in section.rows_from(columns) if row.is_present("工序名称")]
            return "NG", "、".join(names)

        @rule("第二项", clause_id="B")
        def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
            section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
            columns = section.columns("工序名称")
            names = [str(row.value("工序名称")) for row in section.rows_from(columns) if row.is_present("工序名称")]
            return "UNABLE", "、".join(names)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序号", "工序名称", "操作描述"])
    sheet.append(["10", "焊接", "焊接操作"])
    path = tmp_path / "shared.xlsx"
    workbook.save(path)
    workbook.close()
    calls = {"header": 0}
    original = resolver.find_header_row

    def count_header(*args, **kwargs):
        calls["header"] += 1
        return original(*args, **kwargs)

    resolver.find_header_row = count_header
    agent = SharedAudit(AuditInput(audit_id="flow", file_url=str(path)), rule_input(("A", "第一项"), ("B", "第二项")))
    try:
        first, second = [check.check() for check in agent.checks or ()]
    finally:
        resolver.find_header_row = original
        agent.close()
    assert calls["header"] == 1
    assert first is not None and first.status == "NG"
    assert "只属于第一项" in first.suggestions
    assert second is not None and second.status == "UNABLE"
    assert second.suggestions == "焊接"
    assert "只属于第一项" not in second.suggestions


def test_explicit_resource_joins_the_registry_resource(tmp_path: Path) -> None:
    replacement = SheetModel(canonical_name="过程流程图", include_keywords=("仅任务资源",))
    added = SheetModel(canonical_name="任务附加表", include_keywords=("附加",))
    explicit = RuleResource(
        sheets=MappingProxyType({replacement.canonical_name: replacement, added.canonical_name: added}),
        sections=MappingProxyType({}),
        columns=MappingProxyType({}),
        field_blocks=MappingProxyType({}),
        fields=MappingProxyType({}),
        tokens=DomainTokensModel(),
    )
    agent = ProcessFlowChartExcelAgent(
        AuditInput(audit_id="target", file_url=str(tmp_path / "resource.xlsx")),
        rule_input(("1", "local_excel_check")),
        resource=explicit,
    )
    try:
        resource = agent._rule_resource
        assert resource is not None
        assert resource.sheets["过程流程图"].include_keywords == ("仅任务资源",)
        assert "任务附加表" in resource.sheets
        assert "过程流程主数据区" in resource.sections
        assert "操作描述" in resource.columns
    finally:
        agent.close()


def test_process_flow_clause_order_does_not_decide_which_check_runs(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序名称", "操作描述", "产品特殊特性", "产品特性类别", "过程特殊特性", "过程特性类别", "制造", "运输", "存储", "检验", "返工", "报废"])
    sheet.append(["焊接", None, None, None, None, None, "制造", None, None, None, None, None])
    path = tmp_path / "flow.xlsx"
    workbook.save(path)
    workbook.close()

    categories = ProcessFlowChartExcelAgent.EXPECTED_RULE_CATEGORIES
    ordered = [("4", categories[3]), *[(str(index), categories[index - 1]) for index in range(1, 10) if index != 4]]
    results = run_audit(AuditInput(audit_id="flow", file_url=str(path)), rule_input(*ordered))

    assert [result.clause_id for result in results] == [str(index) for index in range(1, 10)]
    by_id = {result.clause_id: result for result in results}
    assert by_id["4"].status == "NG"
    assert "操作描述" in by_id["4"].suggestions
    assert results[0].clause_id == "1"
    assert "操作描述" not in results[0].suggestions
    agent = ProcessFlowChartExcelAgent(AuditInput(audit_id="flow", file_url=str(path)), rule_input(*ordered))
    try:
        indexed = agent.check(0)
    finally:
        agent.close()
    assert indexed is not None
    assert indexed.clause_id == "1"
    assert indexed.status == results[0].status
    assert indexed.suggestions == results[0].suggestions


class QuantAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("范围",)
    action = "all-complete"

    @rule("范围", clause_id="A")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        action = type(self).action
        if action == "all-complete":
            ctx.require_all()
            ctx.record_execution()
            ctx.mark_scope_complete()
            return "PASS", "全部满足"
        if action == "all-partial":
            ctx.require_all()
            ctx.record_execution()
            return "PASS", "看起来全部满足"
        if action == "all-open":
            ctx.require_all()
            ctx.record_execution()
            ctx.mark_scope_complete()
            ctx.note_unresolved("印章尚未辨认")
            return "PASS", "其余已检查"
        if action == "one-witness":
            ctx.require_one()
            ctx.mark_witness()
            ctx.note_unresolved("其余行未读完")
            return "PASS", "已看到一个符合项"
        if action == "one-question":
            ctx.require_one()
            ctx.mark_witness()
            ctx.ask(
                "stamp",
                "印章按哪种理解？",
                (("round", "圆章", "按圆形印章理解"), ("square", "方章", "按方形印章理解")),
            )
            return "PASS", "已看到一个符合项"
        if action == "one-missing":
            ctx.require_one()
            ctx.record_execution()
            ctx.note_unresolved("对照文件没有打开")
            return "PASS", "未找到见证"
        if action == "unable-open":
            ctx.record_execution()
            ctx.note_unresolved("单元格公式没有缓存")
            return "UNABLE", "读取不足"
        if action == "pass-open":
            ctx.record_execution()
            ctx.mark_scope_complete()
            ctx.note_unresolved("签章没有辨认")
            return "PASS", "其余看起来通过"
        if action == "ng-open":
            ctx.require_all()
            ctx.record_execution()
            ctx.mark_scope_complete()
            ctx.note_unresolved("另一字段仍未读取")
            ctx.record_ng("已有明确反例")
            return "NG", "已有明确反例"
        ctx.note_search(found=False, exhausted=False, label="基准文件")
        ctx.require_all()
        ctx.record_execution()
        ctx.mark_scope_complete()
        return "NG", "不能把未完成搜索当成不存在"


def test_observation_keeps_zero_and_false_and_separates_blank_from_missing(tmp_path: Path) -> None:
    from audit.engine.rule_engine.fluent import Table, classify_cell

    assert classify_cell(0) == classify_cell(0)
    assert classify_cell(0).state == "known" and classify_cell(0).value == 0
    assert classify_cell(False).state == "known" and classify_cell(False).value is False
    assert classify_cell(None).state == "blank" and classify_cell(None).value is None
    assert classify_cell("  ").state == "blank"

    class ObserveAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True
        EXPECTED_RULE_CATEGORIES = ("观察",)

        @rule("观察", clause_id="A")
        def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
            section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
            columns = section.columns("工序名称", "操作描述", optional=("过程特殊特性", "产品特性类别", "产品特殊特性"))
            row = next(row for row in section.rows_from(columns) if row.is_present("工序名称"))
            parts = []
            for name in ("工序名称", "操作描述", "过程特殊特性", "产品特性类别", "产品特殊特性"):
                item = row.observation(name)
                parts.append(f"{name}:{item.state}:{item.value!r}")
            return "UNABLE", "|".join(parts)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序号", "工序名称", "操作描述", "过程特殊特性", "产品特性类别"])
    sheet.append(["10", "焊接", 0, False, None])
    path = tmp_path / "observed.xlsx"
    workbook.save(path)
    workbook.close()
    agent = ObserveAudit(AuditInput(audit_id="flow", file_url=str(path)), rule_input(("A", "观察")))
    try:
        result = [check.check() for check in agent.checks or ()][0]
    finally:
        agent.close()
    assert result.status == "UNABLE"
    assert "工序名称:known:'焊接'" in result.suggestions
    assert "操作描述:known:0" in result.suggestions
    assert "过程特殊特性:known:False" in result.suggestions
    assert "产品特性类别:blank:None" in result.suggestions
    assert "产品特殊特性:missing:None" in result.suggestions


def test_an_excel_error_is_unresolved_and_cannot_pass(tmp_path: Path) -> None:
    class ErrorAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True
        EXPECTED_RULE_CATEGORIES = ("观察",)

        @rule("观察", clause_id="A")
        def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
            section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
            columns = section.columns("工序名称", "操作描述")
            row = next(item for item in section.rows_from(columns) if item.is_present("工序名称"))
            observed = row.observation("操作描述")
            if observed.state != "unresolved" or observed.value != "#DIV/0!":
                return "NG", f"{observed.state}:{observed.value}"
            return "PASS", "错误值被当成已检查"

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序号", "工序名称", "操作描述"])
    sheet.append(["10", "焊接", None])
    error = sheet.cell(2, 3)
    error.value = "#DIV/0!"
    error.data_type = "e"
    path = tmp_path / "error.xlsx"
    workbook.save(path)
    workbook.close()
    agent = ErrorAudit(AuditInput(audit_id="flow", file_url=str(path)), rule_input(("A", "观察")))
    try:
        result = [check.check() for check in agent.checks or ()][0]
    finally:
        agent.close()
    assert result.status == "UNABLE"
    assert "不能判为通过" in result.suggestions
    assert "单元格内容无法读取" in result.suggestions


def test_a_stopped_row_scan_does_not_count_as_complete_scope(tmp_path: Path) -> None:
    from audit.engine.rule_engine.fluent import Table

    class StopAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True
        EXPECTED_RULE_CATEGORIES = ("范围",)

        @rule("范围", clause_id="A")
        def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.require_all()
            section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
            columns = section.columns("工序名称", optional=("操作描述",))
            for row in section.rows_from(columns):
                if row.is_present("工序名称"):
                    break
            return "PASS", "只看了第一行"

    class FullAudit(StopAudit):
        @rule("范围", clause_id="A")
        def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.require_all()
            section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
            columns = section.columns("工序名称", optional=("操作描述",))
            names = [str(row.value("工序名称")) for row in section.rows_from(columns) if row.is_present("工序名称")]
            if names != ["焊接", "检验"]:
                return "NG", "、".join(names)
            return "PASS", "两行都已检查"

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序号", "工序名称", "操作描述"])
    sheet.append(["10", "焊接", "焊接操作"])
    sheet.append(["20", "检验", "检验操作"])
    path = tmp_path / "flow.xlsx"
    workbook.save(path)
    workbook.close()
    audit_file = AuditInput(audit_id="flow", file_url=str(path))
    clause_rule = rule_input(("A", "范围"))

    stopped = StopAudit(audit_file, clause_rule)
    try:
        stopped_result = [check.check() for check in stopped.checks or ()][0]
    finally:
        stopped.close()
    assert stopped_result.status == "UNABLE"
    assert stopped_result.scope is None
    assert "范围不完整" in stopped_result.suggestions

    finished = FullAudit(audit_file, clause_rule)
    try:
        finished_result = [check.check() for check in finished.checks or ()][0]
    finally:
        finished.close()
    assert finished_result.status == "PASS"
    assert finished_result.suggestions == "两行都已检查"
    assert finished_result.scope == {"total": 2, "checked": 2, "unknown": 0}


def test_quantifier_pass_needs_complete_scope_or_one_witness(tmp_path: Path) -> None:
    def run(action: str):
        QuantAudit.action = action
        agent = QuantAudit(AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)), rule_input(("A", "范围")))
        try:
            result = [check.check() for check in agent.checks or ()][0]
        finally:
            agent.close()
        return result

    complete = run("all-complete")
    assert complete.status == "PASS"
    assert complete.suggestions == "全部满足"

    partial = run("all-partial")
    assert partial.status == "UNABLE"
    assert "范围不完整" in partial.suggestions

    opened = run("all-open")
    assert opened.status == "UNABLE"
    assert "未解问题" in opened.suggestions
    assert "印章尚未辨认" in opened.suggestions

    witness = run("one-witness")
    assert witness.status == "PASS"
    assert "未完成全量扫描" in witness.suggestions
    assert "其余行未读完" in witness.suggestions

    questioned = run("one-question")
    assert questioned.status == "UNABLE"
    assert "含义未确认" in questioned.suggestions
    assert "印章按哪种理解" in questioned.suggestions
    assert questioned.clarifications[0]["id"] == "stamp"
    assert questioned.clarifications[0]["clause_id"] == "A"

    missing = run("one-missing")
    assert missing.status == "UNABLE"
    assert "没有有效见证" in missing.suggestions
    assert "对照文件没有打开" in missing.suggestions

    unable = run("unable-open")
    assert unable.status == "UNABLE"
    assert "读取不足" in unable.suggestions
    assert "单元格公式没有缓存" in unable.suggestions

    blocked = run("pass-open")
    assert blocked.status == "UNABLE"
    assert "仍有未解问题" in blocked.suggestions
    assert "签章没有辨认" in blocked.suggestions

    counterexample = run("ng-open")
    assert counterexample.status == "NG"
    assert "已有明确反例" in counterexample.suggestions
    assert "另一字段仍未读取" in counterexample.suggestions

    search = run("search-open")
    assert search.status == "NG"
    assert "不能确认不存在" in search.suggestions


def test_an_open_question_cannot_become_not_applicable(tmp_path: Path) -> None:
    class NotApplicableQuestion(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("印章", clause_id="A")
        def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.ask(
                "stamp",
                "印章按哪种理解？",
                (("round", "圆章", "按圆形印章理解"), ("square", "方章", "按方形印章理解")),
            )
            return "NA", "看起来不适用"

    agent = NotApplicableQuestion(AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)), rule_input(("A", "印章")))
    try:
        result = [check.check() for check in agent.checks or ()][0]
    finally:
        agent.close()

    assert result.status == "UNABLE"
    assert result.clause_id == "A"
    assert "含义未确认" in result.suggestions
    assert "印章按哪种理解" in result.suggestions
    assert "看起来不适用" not in result.suggestions
    assert result.clarifications[0]["id"] == "stamp"
    assert result.clarifications[0]["clause_id"] == "A"


def test_a_collected_counterexample_stays_visible_when_the_method_does_not_return_ng(tmp_path: Path) -> None:
    class CounterexampleAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("读取不足", clause_id="UNABLE")
        def unable(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_ng("工序名称为空")
            ctx.note_unresolved("印章尚未辨认")
            return "UNABLE", "还有单元格没读完"

        @rule("不适用", clause_id="NA")
        def not_applicable(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_ng("已发现空值")
            return "NA", "看起来不适用"

        @rule("技术失败", clause_id="ERR")
        def failed(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_ng("已经发现的空值")
            ctx.note_unresolved("公式没有缓存")
            return "ERR", "检查中断"

    agent = CounterexampleAudit(
        AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)),
        rule_input(("UNABLE", "读取不足"), ("NA", "不适用"), ("ERR", "技术失败")),
    )
    try:
        results = [check.check() for check in agent.checks or ()]
    finally:
        agent.close()

    by_id = {result.clause_id: result for result in results}
    assert by_id["UNABLE"].status == "NG"
    assert "工序名称为空" in by_id["UNABLE"].suggestions
    assert "还有单元格没读完" in by_id["UNABLE"].suggestions
    assert "印章尚未辨认" in by_id["UNABLE"].suggestions
    assert by_id["NA"].status == "NG"
    assert "已发现空值" in by_id["NA"].suggestions
    assert "看起来不适用" in by_id["NA"].suggestions
    assert by_id["ERR"].status == "ERR"
    assert "检查中断" in by_id["ERR"].suggestions
    assert "已经发现的空值" in by_id["ERR"].suggestions
    assert "公式没有缓存" in by_id["ERR"].suggestions


def test_a_guard_stop_keeps_the_counterexample_and_the_open_problem(tmp_path: Path) -> None:
    from audit.engine.rule_engine.contracts import Decision

    class GuardAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("缺表", clause_id="NG")
        def missing_sheet(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_execution()
            ctx.record_ng("已看到空操作描述")
            ctx.note_unresolved("另一页未打开")
            ctx.sheet("不存在的表")
            return "PASS", "不会执行到这里"

        @rule("定位失败", clause_id="ERR")
        def locate(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_execution()
            ctx.record_ng("工序名称为空")
            ctx.note_unresolved("印章尚未辨认")
            ctx._fail(Decision.ERR, "列定位失败")
            return "PASS", "不会执行到这里"

    agent = GuardAudit(
        AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)),
        rule_input(("NG", "缺表"), ("ERR", "定位失败")),
    )
    try:
        results = [check.check() for check in agent.checks or ()]
    finally:
        agent.close()

    by_id = {result.clause_id: result for result in results}
    assert by_id["NG"].status == "NG"
    assert by_id["NG"].checks_performed == 1
    assert "不存在的表" in by_id["NG"].suggestions
    assert "已看到空操作描述" in by_id["NG"].suggestions
    assert "另一页未打开" in by_id["NG"].suggestions
    assert by_id["ERR"].status == "ERR"
    assert by_id["ERR"].checks_performed == 1
    assert "列定位失败" in by_id["ERR"].suggestions
    assert "工序名称为空" in by_id["ERR"].suggestions
    assert "印章尚未辨认" in by_id["ERR"].suggestions


def test_a_clause_exception_keeps_the_counterexample_and_the_open_question(tmp_path: Path) -> None:
    class BoomAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("中断", clause_id="A")
        def boom(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_execution()
            ctx.record_ng("工序名称为空")
            ctx.note_unresolved("印章尚未辨认")
            ctx.ask(
                "stamp",
                "印章按哪种理解？",
                (("round", "圆章", "按圆形印章理解"), ("square", "方章", "按方形印章理解")),
            )
            raise RuntimeError("读取器中断")

    agent = BoomAudit(AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)), rule_input(("A", "中断")))
    try:
        result = agent.check(0)
    finally:
        agent.close()

    assert result is not None
    assert result.clause_id == "A"
    assert result.status == "ERR"
    assert result.checks_performed == 1
    assert "读取器中断" in result.suggestions
    assert "工序名称为空" in result.suggestions
    assert "印章尚未辨认" in result.suggestions
    assert result.clarifications[0]["id"] == "stamp"
    assert result.clarifications[0]["clause_id"] == "A"
    assert result.clarifications[0]["candidates"][0]["id"] == "round"


def test_a_malformed_return_keeps_the_counterexample_and_the_open_question(tmp_path: Path) -> None:
    class BadReturnAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("返回", clause_id="A")
        def bad(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_execution()
            ctx.record_ng("工序名称为空")
            ctx.note_unresolved("印章尚未辨认")
            ctx.ask(
                "stamp",
                "印章按哪种理解？",
                (("round", "圆章", "按圆形印章理解"), ("square", "方章", "按方形印章理解")),
            )
            return "PASS"  # type: ignore[return-value]

    agent = BadReturnAudit(AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)), rule_input(("A", "返回")))
    try:
        result = agent.check(0)
    finally:
        agent.close()

    assert result is not None
    assert result.clause_id == "A"
    assert result.status == "ERR"
    assert result.checks_performed == 1
    assert "检查没有返回状态和说明" in result.suggestions
    assert "工序名称为空" in result.suggestions
    assert "印章尚未辨认" in result.suggestions
    assert result.clarifications[0]["id"] == "stamp"
    assert result.clarifications[0]["clause_id"] == "A"


def test_a_method_without_clause_id_is_not_bound_by_index(tmp_path: Path) -> None:
    class UnnumberedAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True
        EXPECTED_RULE_CATEGORIES = ("未编号", "已编号")
        calls: list[str]

        def __init__(self, audit_file: AuditInput, rule: RuleInput) -> None:
            self.calls = []
            super().__init__(audit_file, rule)

        @rule("未编号")
        def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
            self.calls.append("rule_1")
            return "PASS", "不应按序号执行"

        @rule("已编号", clause_id="R4")
        def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
            self.calls.append("rule_2")
            return "NA", "保留身份"

    agent = UnnumberedAudit(
        AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)),
        rule_input(("1", "第一项"), ("R4", "已编号")),
    )
    try:
        results = [check.check() for check in agent.checks or ()]
    finally:
        agent.close()

    assert [(result.clause_id, result.status) for result in results] == [("1", "ERR"), ("R4", "NA"), (None, "ERR")]
    assert "没有对应的检查入口" in results[0].suggestions
    assert "检查入口缺少条款 ID" in results[2].suggestions
    assert agent.calls == ["rule_2"]


def test_a_clause_method_that_returns_none_names_the_missing_result(tmp_path: Path) -> None:
    class MissingReturnAudit(ExcelAgent):
        CLAUSE_BOUND_CHECKS = True

        @rule("操作描述", clause_id="R4")
        def check_r4(self, ctx: RuleContext):
            del ctx
            return None

    agent = MissingReturnAudit(
        AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)),
        rule_input(("R4", "操作描述")),
    )
    try:
        result = (agent.checks or ())[0].check()
    finally:
        agent.close()

    assert result is not None
    assert result.clause_id == "R4"
    assert result.status == "ERR"
    assert "方法必须 return 状态, 说明" in result.suggestions
    assert "TypeError" not in result.suggestions


def test_rule_check_reports_none_for_a_clause_without_an_agent() -> None:
    from audit.engine.checkers.rule_check import RuleCheck

    result = RuleCheck(RuleItem(id="R4", category="操作描述", rule_to_check="操作描述", type_of_rule=""), lambda: None).check()
    assert result.status == "ERR"
    assert result.clause_id == "R4"
    assert "没有返回结果" in result.suggestions


def test_a_question_keeps_cells_read_after_it_was_asked(tmp_path: Path) -> None:
    class LateReadAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("后读", clause_id="A")
        def late(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.ask("header-meaning", "表头按什么理解？", (("left", "左列", "按左列表头理解"), ("right", "右列", "按右列表头理解")))
            ctx.note_cell("过程流程图", 1, 1)
            return "PASS", "先问后读"

    path = tmp_path / "late.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet["A1"] = "工序名称"
    workbook.save(path)
    workbook.close()
    agent = LateReadAudit(
        AuditInput(audit_id="target", file_url=str(path), file_metadata={"file_version_id": "target-version"}),
        rule_input(("A", "后读")),
    )
    try:
        result = [check.check() for check in agent.checks or ()][0]
    finally:
        agent.close()
    assert result.status == "UNABLE"
    assert result.clarifications[0]["sources"] == [
        {
            "document_id": "target-version",
            "locator": {"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A1"},
            "excerpt": "工序名称",
        }
    ]


def test_a_question_uses_the_bound_workbook_value_not_the_target_sheet() -> None:
    target = Workbook()
    target.active.title = "特殊特性清单"
    target.active["B2"] = "目标文件的值"
    related = Workbook()
    related.active.title = "特殊特性清单"
    related.active["B2"] = "关联文件的值"
    resource = RuleResource(
        sheets=MappingProxyType({}),
        sections=MappingProxyType({}),
        columns=MappingProxyType({}),
        field_blocks=MappingProxyType({}),
        fields=MappingProxyType({}),
        tokens=DomainTokensModel(),
    )
    ctx = RuleContext(None, target, resource, file_version_id="target-version")
    ctx.bind_workbook(target, "target-version")
    ctx.bind_workbook(related, "related-version")
    ctx.note_cell("特殊特性清单", 2, 2, file_version_id="related-version", excerpt="伪造摘录")
    ctx.ask("list-meaning", "按哪份清单理解？", (("target", "目标", "按目标文件理解"), ("related", "关联", "按关联文件理解")))
    source = ctx.questions()[0]["sources"][0]
    assert source["document_id"] == "related-version"
    assert source["excerpt"] == "关联文件的值"
    assert source["locator"] == {"kind": "xlsx_cell", "sheet": "特殊特性清单", "cell": "B2"}
    target.close()
    related.close()


def test_related_cell_reads_do_not_erase_the_target_file() -> None:
    from audit.engine.rule_engine.context import MAX_CELL_READS

    resource = RuleResource(
        sheets=MappingProxyType({}),
        sections=MappingProxyType({}),
        columns=MappingProxyType({}),
        field_blocks=MappingProxyType({}),
        fields=MappingProxyType({}),
        tokens=DomainTokensModel(),
    )
    target = Workbook()
    target.active.title = "过程流程图"
    target.active["A2"] = "焊接"
    related_book = Workbook()
    related_book.active.title = "特殊特性清单"
    for row in range(1, MAX_CELL_READS + 9):
        related_book.active.cell(row, 1, str(row))
    ctx = RuleContext(None, target, resource, file_version_id="target-version")
    ctx.bind_workbook(target, "target-version")
    ctx.bind_workbook(related_book, "related-version")
    ctx.note_cell("过程流程图", 2, 1, file_version_id="target-version", excerpt="伪造摘录")
    ctx.note_cell("不存在的表", 1, 1, file_version_id="target-version")
    for row in range(1, MAX_CELL_READS + 9):
        ctx.note_cell("特殊特性清单", row, 1, file_version_id="related-version")

    reads = ctx.cell_reads()
    assert ("target-version", "过程流程图", 2, 1) in reads
    assert ctx.cell_excerpt("target-version", "过程流程图", 2, 1) == "焊接"
    assert all(item[1] != "不存在的表" for item in reads)
    related = [item for item in reads if item[0] == "related-version"]
    assert len(related) == MAX_CELL_READS
    assert related[0] == ("related-version", "特殊特性清单", 9, 1)
    assert ctx.cell_reads_truncated()


def two_operation_workbook(tmp_path: Path) -> str:
    path = tmp_path / "flow.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序名称", "操作描述"])
    sheet.append(["焊接", "焊装"])
    sheet.append(["装配", "组装"])
    workbook.save(path)
    workbook.close()
    return str(path)


class EarlyStopAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("提前停止",)

    @rule("提前停止", clause_id="ALL")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
        for row in section.rows_from(section.columns("工序名称")):
            row.value("工序名称")
            break
        return "PASS", "只看了第一行"


class FullScanAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("完整扫描",)

    @rule("完整扫描", clause_id="ALL")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
        rows = list(section.rows_from(section.columns("工序名称")))
        for row in rows:
            row.value("工序名称")
        return "PASS", f"已检查{len(rows)}行"


class OneWitnessAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("存在一个",)

    @rule("存在一个", clause_id="ONE")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        ctx.require_one()
        section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
        for row in section.rows_from(section.columns("工序名称")):
            if row.is_present("工序名称"):
                ctx.mark_witness()
                return "PASS", "找到一个工序"
        return "UNABLE", "没有工序"


def test_related_workbook_reads_keep_their_own_file_version(tmp_path: Path) -> None:
    import json

    from audit.engine.rule_engine.fluent import Cell

    class ReadAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("跨文件", clause_id="R9")
        def check_related(self, ctx: RuleContext) -> tuple[str, str]:
            Cell(ctx, ctx.workbook.active, 2, 1).value()
            related_path = tmp_path / "list.xlsx"
            related_book = Workbook()
            related_sheet = related_book.active
            related_sheet.title = "特殊特性清单"
            related_sheet["B2"] = "长度"
            related_book.save(related_path)
            related_book.close()
            from openpyxl import load_workbook

            opened = load_workbook(related_path)
            ctx.bind_workbook(opened, "related-version")
            Cell(ctx, opened.active, 2, 2).value()
            opened.close()
            ctx.record_execution()
            return "NG", "已读取目标文件和关联清单"

    path = tmp_path / "flow.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "过程流程图"
    sheet["A2"] = "焊接"
    book.save(path)
    book.close()
    agent = ReadAudit(
        AuditInput(audit_id="target", file_url=str(path), file_metadata={"file_version_id": "target-version"}),
        rule_input(("R9", "跨文件")),
    )
    try:
        result = agent.check(0)
    finally:
        agent.close()

    assert result is not None
    assert result.evidence is not None
    located = [json.loads(source.locator) for source in result.evidence.sources]
    by_cell = {item["cell"]: item for item in located}
    assert by_cell["A2"]["file_version_id"] == "target-version"
    assert by_cell["A2"]["sheet"] == "过程流程图"
    assert by_cell["B2"]["file_version_id"] == "related-version"
    assert by_cell["B2"]["sheet"] == "特殊特性清单"
    excerpts = {json.loads(source.locator)["cell"]: source.excerpt for source in result.evidence.sources}
    assert excerpts == {"A2": "焊接", "B2": "长度"}


def test_known_zero_and_false_stay_in_the_cell_excerpt(tmp_path: Path) -> None:
    import json

    from audit.engine.rule_engine.fluent import Cell

    class ValueAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("原值", clause_id="R1")
        def check_values(self, ctx: RuleContext) -> tuple[str, str]:
            sheet = ctx.workbook.active
            Cell(ctx, sheet, 1, 1).value()
            Cell(ctx, sheet, 1, 2).value()
            ctx.record_execution()
            return "NG", "已读取已知值"

    path = tmp_path / "values.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "过程流程图"
    sheet["A1"] = 0
    sheet["B1"] = False
    book.save(path)
    book.close()
    agent = ValueAudit(AuditInput(audit_id="target", file_url=str(path), file_metadata={"file_version_id": "target-version"}), rule_input(("R1", "原值")))
    try:
        result = agent.check(0)
    finally:
        agent.close()

    assert result is not None and result.evidence is not None
    assert result.checks_performed == 3
    assert result.scope is None
    excerpts = {json.loads(source.locator)["cell"]: source.excerpt for source in result.evidence.sources}
    assert excerpts == {"A1": "0", "B1": "False"}


def test_an_unbound_workbook_cell_is_not_recorded_as_the_target(tmp_path: Path) -> None:
    import json

    from audit.engine.rule_engine.fluent import Cell

    class ReadAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("跨文件", clause_id="R9")
        def check_related(self, ctx: RuleContext) -> tuple[str, str]:
            Cell(ctx, ctx.workbook.active, 2, 1).value()
            related_path = tmp_path / "list.xlsx"
            related_book = Workbook()
            related_sheet = related_book.active
            related_sheet.title = "特殊特性清单"
            related_sheet["B2"] = "长度"
            related_book.save(related_path)
            related_book.close()
            from openpyxl import load_workbook

            opened = load_workbook(related_path)
            Cell(ctx, opened.active, 2, 2).value()
            opened.close()
            ctx.record_execution()
            return "NG", "关联工作簿没有绑定版本"

    path = tmp_path / "flow.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "过程流程图"
    sheet["A2"] = "焊接"
    book.save(path)
    book.close()
    agent = ReadAudit(
        AuditInput(audit_id="target", file_url=str(path), file_metadata={"file_version_id": "target-version"}),
        rule_input(("R9", "跨文件")),
    )
    try:
        result = agent.check(0)
    finally:
        agent.close()

    assert result is not None
    assert result.evidence is not None
    located = [json.loads(source.locator) for source in result.evidence.sources]
    assert located == [{"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A2", "file_version_id": "target-version"}]


def test_a_returned_ng_summary_is_not_wrapped_again(tmp_path: Path) -> None:
    agent = SummaryAudit(AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)), rule_input(("B", "第二项")))
    try:
        result = [check.check() for check in agent.checks or () if check.rule_item.id == "B"][0]
    finally:
        agent.close()

    assert result.status == "NG"
    assert result.suggestions.count("检测到") == 1
    assert result.suggestions.count("已收集的不合格") == 1


def test_a_clause_method_does_not_need_a_numbered_rule_name(tmp_path: Path) -> None:
    class NamedAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("步骤内容", clause_id="STEP")
        def check_steps(self, ctx: RuleContext) -> tuple[str, str]:
            del ctx
            return "UNABLE", "未读取步骤"

    agent = NamedAudit(AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)), rule_input(("STEP", "步骤内容")))
    try:
        assert [name for name, _description in agent.declared_rules()] == ["check_steps"]
        result = agent.check(0)
    finally:
        agent.close()

    assert result is not None
    assert result.clause_id == "STEP"
    assert result.status == "UNABLE"


def test_an_interrupted_scan_cannot_pass_but_a_witness_can(tmp_path: Path) -> None:
    early = EarlyStopAudit(AuditInput(audit_id="target", file_url=two_operation_workbook(tmp_path)), rule_input(("ALL", "提前停止")))
    try:
        stopped = early.check(0)
    finally:
        early.close()
    assert stopped is not None
    assert stopped.clause_id == "ALL"
    assert stopped.status == "UNABLE"
    assert stopped.scope is None
    assert "范围不完整" in stopped.suggestions
    assert "扫描未完成" in stopped.suggestions

    complete = FullScanAudit(AuditInput(audit_id="target", file_url=two_operation_workbook(tmp_path)), rule_input(("ALL", "完整扫描")))
    try:
        finished = complete.check(0)
    finally:
        complete.close()
    assert finished is not None
    assert finished.status == "PASS"
    assert finished.suggestions == "已检查2行"
    assert finished.scope == {"total": 2, "checked": 2, "unknown": 0}

    class TwiceScanAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("扫两次", clause_id="ALL")
        def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
            section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
            columns = section.columns("工序名称")
            for row in section.rows_from(columns):
                row.value("工序名称")
            for row in section.rows_from(columns):
                row.value("工序名称")
            return "PASS", "扫了两次"

    twice = TwiceScanAudit(AuditInput(audit_id="target", file_url=two_operation_workbook(tmp_path)), rule_input(("ALL", "扫两次")))
    try:
        repeated_result = twice.check(0)
    finally:
        twice.close()
    assert repeated_result is not None
    assert repeated_result.status == "PASS"
    assert repeated_result.scope is None

    witness = OneWitnessAudit(AuditInput(audit_id="target", file_url=two_operation_workbook(tmp_path)), rule_input(("ONE", "存在一个")))
    try:
        found = witness.check(0)
    finally:
        witness.close()
    assert found is not None
    assert found.clause_id == "ONE"
    assert found.status == "PASS"
    assert found.scope is None
    assert "找到一个工序" in found.suggestions
    assert "未完成全量扫描" in found.suggestions


def test_all_required_matches_can_keep_an_explicit_empty_pass(tmp_path: Path) -> None:
    class AllEmptyAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("全部", clause_id="ALL")
        def all_empty(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.require_all()
            ctx.record_empty_scope("PASS", "规则写明没有工序即通过")
            return "PASS", "方法末尾通过"

    agent = AllEmptyAudit(AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)), rule_input(("ALL", "全部")))
    try:
        result = [check.check() for check in agent.checks or ()][0]
    finally:
        agent.close()

    assert result.clause_id == "ALL"
    assert result.status == "PASS"
    assert result.suggestions == "规则写明没有工序即通过"
    assert result.empty_scope_basis == "规则写明没有工序即通过"


def test_one_required_match_can_keep_an_explicit_empty_pass(tmp_path: Path) -> None:
    class OneEmptyAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("存在一个", clause_id="ONE")
        def one_empty(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.require_one()
            ctx.record_empty_scope("PASS", "规则写明没有工序即通过")
            return "PASS", "方法末尾通过"

    agent = OneEmptyAudit(AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)), rule_input(("ONE", "存在一个")))
    try:
        result = [check.check() for check in agent.checks or ()][0]
    finally:
        agent.close()

    assert result.clause_id == "ONE"
    assert result.status == "PASS"
    assert result.suggestions == "规则写明没有工序即通过"
    assert result.empty_scope_basis == "规则写明没有工序即通过"


def test_a_scan_that_yields_rows_cannot_pass_as_an_empty_scope(tmp_path: Path) -> None:
    from audit.engine.rule_engine.fluent import Table

    class OccupiedScanAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("有工序", clause_id="ROWS")
        def occupied(self, ctx: RuleContext) -> tuple[str, str]:
            section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
            rows = list(section.rows_from(section.columns("工序名称")))
            assert rows
            ctx.record_empty_scope("PASS", "规则写明没有工序即通过")
            return "PASS", "方法末尾通过"

    path = tmp_path / "occupied.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序号", "工序名称", "操作描述"])
    sheet.append(["10", "焊接", "焊接操作"])
    workbook.save(path)
    workbook.close()
    agent = OccupiedScanAudit(AuditInput(audit_id="target", file_url=str(path)), rule_input(("ROWS", "有工序")))
    try:
        result = [check.check() for check in agent.checks or ()][0]
    finally:
        agent.close()

    assert result.clause_id == "ROWS"
    assert result.status == "ERR"
    assert result.empty_scope_basis is None
    assert "已经扫到数据行" in result.suggestions
    assert "规则写明没有工序即通过" not in result.suggestions


def test_explicit_empty_scope_keeps_its_basis_and_does_not_hide_collected_ng(tmp_path: Path) -> None:
    class EmptyScopeAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True
        EXPECTED_RULE_CATEGORIES = ("可以通过", "不合格", "空范围", "零断言")

        @rule("空范围", clause_id="EMPTY")
        def empty_scope(self, ctx: RuleContext) -> tuple[str, str]:
            assert ctx.empty_scope() is None
            assert ctx.execution_count == 0
            ctx.record_empty_scope("NA", "规则写明没有工序时不适用")
            return "PASS", "方法末尾通过"

        @rule("可以通过", clause_id="ALLOW")
        def allowed(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_empty_scope("PASS", "规则写明没有工序即通过")
            return "PASS", "方法末尾通过"

        @rule("不合格", clause_id="BAD")
        def collected(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_empty_scope("PASS", "空范围可以通过")
            ctx.record_ng("已收集的不合格")
            return "PASS", "方法末尾通过"

        @rule("零断言", clause_id="ZERO")
        def zero(self, ctx: RuleContext) -> tuple[str, str]:
            return "PASS", "只写了建议"

    agent = EmptyScopeAudit(AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)), rule_input(("EMPTY", "空范围"), ("ALLOW", "可以通过"), ("BAD", "不合格"), ("ZERO", "零断言")))
    try:
        first = [check.check() for check in agent.checks or ()]
        second = agent.checks[0].check()
    finally:
        agent.close()

    by_id = {result.clause_id: result for result in first}
    assert (by_id["EMPTY"].status, by_id["EMPTY"].suggestions) == ("NA", "规则写明没有工序时不适用")
    assert (by_id["ALLOW"].status, by_id["ALLOW"].suggestions) == ("PASS", "规则写明没有工序即通过")
    assert by_id["BAD"].status == "NG"
    assert "已收集的不合格" in by_id["BAD"].suggestions
    assert "空范围可以通过" not in by_id["BAD"].suggestions
    assert by_id["ZERO"].status == "ERR"
    assert "零断言不能判为通过" in by_id["ZERO"].suggestions
    assert second is not None
    assert second.status == "NA"


class _UnboundPassAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART

    @rule("旧通过")
    def old_pass(self, ctx: RuleContext) -> tuple[str, str]:
        return "PASS", "旧检查没有断言计数"


def test_unbound_checker_zero_execution_pass_is_unchanged(tmp_path: Path) -> None:
    agent = _UnboundPassAudit(AuditInput(audit_id="target", file_url=saved_workbook(tmp_path)), rule_input(("1", "旧通过")))
    try:
        result = agent.check(0)
    finally:
        agent.close()

    assert result is not None
    assert result.status == "PASS"
    assert result.suggestions == "旧检查没有断言计数"

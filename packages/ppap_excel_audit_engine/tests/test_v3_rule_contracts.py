import pytest
from openpyxl import Workbook
from ppap_core.contracts import AuditInput, RuleInput
from ppap_excel_audit_engine.checkers.agents_v3_overlay.annual_test_requirements_agent import AnnualTestRequirementsAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.checking_fixture_approval_excel_agent import CheckingFixtureApprovalExcelAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.control_plan_agent import ControlPlanAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.dimension_measurement_excel_agent import DimensionMeasurementExcelAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.dpvr_excel_agent import DpvrExcelAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.dvp_r_excel_agent import DvpRExcelAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.equipment_list_agent import EquipmentListAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.initial_process_capacity_agent import InitialProcessCapacityAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.msa_report_agent import MsaReportAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.pfmea_agent import PfmeaAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.plant_capacity_evaluation_agent import PlantCapacityEvaluationAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.process_flow_chart_excel_agent import ProcessFlowChartExcelAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.process_special_characteristic_agent import ProcessSpecialCharacteristicAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.psw_excel_agent import PswExcelAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.sub_supplier_list_excel_agent import SubSupplierListExcelAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.tooling_approval_agent import ToolingApprovalAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.xir_assembly_issue_agent import XirAssemblyIssueAgent
from ppap_excel_audit_engine.engine import run_audit

# Golden contracts from the frozen source inventory in #152, not model output.
CONTRACTS = [
    (
        SubSupplierListExcelAgent,
        ("单件重量列及精度审核", "材料信息列完整性及非空审核", "部件状态列存在性及互斥性审核", "PPAP完成状态逻辑一致性审核"),
    ),
    (
        MsaReportAgent,
        ("任务项字段识别与状态审核", "测量系统分析计划表完整性审核", "量具重复性和再现性样本数量审核", "量具重复性和再现性结论判定审核"),
    ),
    (
        DimensionMeasurementExcelAgent,
        (
            "CoverSheet页存在性及内容完整性审核",
            "测量报告列存在性及数据非空审核",
            "特定控制方向的公差NA值一致性审核",
            "名义值为空时的偏差值存在性审核",
            "类型列内容合规性审核",
            "测点号与测点图标记点对应性审核",
            "特定表类型的偏差值在公差范围内审核",
            "中性值（Centering）计算准确性审核",
            "中性值（Centering）阈值判定及预警审核",
            "极差值（Range）计算准确性审核",
            "极差值（Range）阈值判定及预警审核",
        ),
    ),
    (
        ProcessFlowChartExcelAgent,
        (
            "表头字段完整性检查",
            "产品特殊特性及类别填写逻辑校验",
            "过程特殊特性及类别填写逻辑校验",
            "工序名称与操作描述填写逻辑校验",
            "工序名称与图例填写逻辑校验",
            "起始工序拆解完整性识别",
            "最终工序拆解完整性识别",
            "返工/返修工序检验环节识别",
            "跨文件一致性审核（PFD与特殊特性清单）",
        ),
    ),
    (InitialProcessCapacityAgent, ("P阶段Cpk结果列审核", "P阶段结果判定列审核", "控制图及短期过程能力审核")),
    (CheckingFixtureApprovalExcelAgent, ("检具清单与认可报告完整性审核", "检具清单标题规范性审核", "所附文件与结论一致性审核")),
    (
        ControlPlanAgent,
        (
            "文件类型勾选审核",
            "过程流程图与控制计划列项及一致性审核",
            "PFMEA与控制计划特殊特性一致性审核",
            "特殊特性分类与产品/过程列存在性审核",
            "特殊特性分类与控制方法关联审核",
            "频率或频次列完整性审核",
            "控制计划表头字段完整性及必填项审核",
            "流程一致性审核",
            "特性一致性审核",
            "措施一致性审核（PFMEA与CP）",
            "措施一致性审核（特殊特性清单与CP）",
        ),
    ),
    (AnnualTestRequirementsAgent, ("质量等级字段识别", "试验项目与测试频率一致性", "供应商名称格式审核", "样本数量列及内容识别", "试验要求栏完整性审核", "车型字段存在性及内容审核")),
    (DpvrExcelAgent, ("文件编号审核", "试验结论审核", "试验类型审核", "印章审核", "试验图片审核", "样品数量审核")),
    (DvpRExcelAgent, ("基础信息字段完整性审核", "试验计划列完整性审核", "PV试验样本状态一致性审核", "计划时间逻辑审核", "实际试验时间逻辑审核", "试验结果有效性审核", "试验报告编号完整性审核")),
    (
        PswExcelAgent,
        (
            "生产件批准交付物文件清单附件检查",
            "重量字段格式检查",
            "印章及授权声明检查",
            "供应商名称格式检查",
            "签字与盖章完整性检查",
            "关键字段非空检查",
            "提交原因选项检查",
            "认可情况及结果勾选检查",
            "条件批准日期时效检查",
            "条件批准补充评论检查",
        ),
    ),
    (ToolingApprovalAgent, ("零件供应商名称与提交日期字段检查", "关键列完整性与内容非空检查", "模具设计尺寸格式检查", "实物照片列检查", "编制与审核字段检查", "实物确认列内容检查")),
    (PfmeaAgent, ("表头字段完整性审核", "PFMEA与变更履历一致性审核", "过程项功能维度审核", "失效影响及严重度审核", "现行探测控制及探测度审核", "流程一致性审核", "特性一致性审核")),
    (EquipmentListAgent, ("设备开发列完整性与数据填充审核", "设备验收列完整性与整改逻辑审核")),
    (PlantCapacityEvaluationAgent, ("基础字段完整性与内容校验", "线体工序数据完整性校验", "风险评估与措施一致性校验", "良率计算逻辑校验", "单线体总产出JPH计算逻辑校验", "预估月度产能计算逻辑校验")),
    (ProcessSpecialCharacteristicAgent, ("产品编号识别", "列完整性及内容非空检查")),
    (XirAssemblyIssueAgent, ("基础字段完整性审核", "问题关闭状态与描述关联审核", "整改措施要素完整性及时间有效性审核", "风险分类标识审核")),
]


def input_rules(agent_class, categories, *, ids=None):
    if ids is None:
        ids = [str(index) for index in range(1, len(categories) + 1)]
    return RuleInput(
        rule_id="confirmed-contract",
        rule_profile="v3",
        type=agent_class.DOC_TYPE.get_type_name(),
        rules=[{"id": rule_id, "category": category, "rule_to_check": f"规则 {index}"} for index, (rule_id, category) in enumerate(zip(ids, categories), 1)],
    )


@pytest.mark.parametrize(("agent_class", "expected"), CONTRACTS, ids=[agent.__name__ for agent, _ in CONTRACTS])
def test_v3_agents_keep_the_frozen_rule_order_and_names(tmp_path, agent_class, expected):
    path = tmp_path / "target.xlsx"
    workbook = Workbook()
    workbook.save(path)
    workbook.close()
    agent = agent_class(AuditInput(audit_id="target", file_url=str(path)), input_rules(agent_class, expected))
    try:
        assert agent.agent_rules == list(expected)
    finally:
        agent.close()


@pytest.mark.parametrize(
    "agent_class",
    [DimensionMeasurementExcelAgent, ProcessFlowChartExcelAgent, PfmeaAgent, ControlPlanAgent, ProcessSpecialCharacteristicAgent],
    ids=lambda agent: agent.__name__,
)
def test_display_category_text_does_not_replace_the_fixed_checker_order(tmp_path, caplog, agent_class):
    expected = next(names for agent, names in CONTRACTS if agent is agent_class)
    categories = ("用户核对的分类名称", *expected[1:])
    agent = agent_class(AuditInput(audit_id="target", file_url=str(tmp_path / "target.xlsx")), input_rules(agent_class, categories))
    try:
        assert agent.agent_rules == list(expected)
        assert "category text differs" in caplog.text
        assert "用户核对的分类名称" in caplog.text
    finally:
        agent.close()


@pytest.mark.parametrize(
    "agent_class",
    [DimensionMeasurementExcelAgent, ProcessFlowChartExcelAgent, PfmeaAgent, ControlPlanAgent, ProcessSpecialCharacteristicAgent],
    ids=lambda agent: agent.__name__,
)
def test_local_fixture_categories_keep_the_same_checker_order(tmp_path, agent_class):
    expected = next(names for agent, names in CONTRACTS if agent is agent_class)
    categories = ("local_excel_check",) * len(expected)
    agent = agent_class(AuditInput(audit_id="target", file_url=str(tmp_path / "target.xlsx")), input_rules(agent_class, categories))
    try:
        assert agent.agent_rules == list(expected)
    finally:
        agent.close()


def test_dimension_internal_rule_id_order_mismatch_is_rejected(tmp_path):
    expected = next(names for agent, names in CONTRACTS if agent is DimensionMeasurementExcelAgent)
    rule_ids = ["2", "1", *[str(index) for index in range(3, len(expected) + 1)]]

    with pytest.raises(RuntimeError, match="rule contract id/order mismatch"):
        DimensionMeasurementExcelAgent(
            AuditInput(audit_id="target", file_url=str(tmp_path / "target.xlsx")),
            input_rules(DimensionMeasurementExcelAgent, expected, ids=rule_ids),
        )


def test_public_engine_reports_the_rule_contract_initialization_error(tmp_path):
    path = tmp_path / "target.xlsx"
    workbook = Workbook()
    workbook.save(path)
    workbook.close()
    expected = next(names for agent, names in CONTRACTS if agent is DimensionMeasurementExcelAgent)
    rule_ids = ["2", "1", *[str(index) for index in range(3, len(expected) + 1)]]
    rules = input_rules(DimensionMeasurementExcelAgent, expected, ids=rule_ids)

    results = run_audit(AuditInput(audit_id="target", file_url=str(path)), rules)

    assert len(results) == 1
    assert results[0].status == "ERR"
    assert results[0].item == rules.type
    assert "Audit engine execution failed:" in results[0].suggestions
    assert "rule contract id/order mismatch" in results[0].suggestions

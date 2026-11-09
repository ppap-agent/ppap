"""Li Auto v5 native decisions through the directory factory and frozen Core.

Cases reuse the literal boundary scenarios of retained v4 unit assets; they do
not change those assets or claim independently verified customer accuracy.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from datetime import date, timedelta
from types import ModuleType, SimpleNamespace

import pytest
from audit.checker_core import FrozenInput, FrozenTarget, RuleBinding, checker_digest, validate_results
from audit.checker_lib.source import SourceAudit
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.standard_library import check_factory
from audit.standard_suites import load_predefined_checkers
from openpyxl import Workbook
from openpyxl.drawing.image import Image as WorkbookImage
from PIL import Image


def _workbook(sheets, *, drawing=False):
    book = Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    if drawing:
        image = io.BytesIO()
        Image.new("RGB", (20, 20), "black").save(image, format="PNG")
        image.seek(0)
        book.worksheets[0].add_image(WorkbookImage(image), "B1")
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def _execute(tmp_path, monkeypatch, entry, clause, sheets, *, visual=None, error=None, drawing=False, peers=(), expected_sha=None, suffix=".xlsx", content=None):
    """Use the actual directory loader, factory adapter and frozen Core validator."""
    suite = next(item for item in load_predefined_checkers() if item.source_path == f"v5/理想/{entry}")
    resources = ModuleType("resources")
    exec(compile(suite.files["resources.py"], f"{suite.source_path}/resources.py", "exec"), resources.__dict__)
    monkeypatch.setitem(sys.modules, "resources", resources)
    checker = ModuleType(f"li_auto_test_{entry}")
    exec(compile(suite.files["checker.py"], f"{suite.source_path}/checker.py", "exec"), checker.__dict__)
    body = json.loads(suite.files["rules.json"])
    body["rules"] = [item for item in body["rules"] if item["id"] == clause]
    body["checker_binding"] = {clause: suite.entrypoints[clause]}
    assert len(body["rules"]) == 1
    content = _workbook(sheets, drawing=drawing) if content is None else content
    digest = hashlib.sha256(content).hexdigest()
    path = tmp_path / (entry + suffix)
    path.write_bytes(content)
    audit = AuditInput(audit_id="frozen-original", file_url=str(path), customer="理想", project_id="project", file_metadata={"sha256": expected_sha or digest})
    rule = RuleInput(rule_id="frozen-rule", **{key: value for key, value in body.items() if key != "rule_id"})
    frozen = [FrozenInput(audit.audit_id, digest, "target", path.name)]
    by_type = {}
    for identity, kind, peer_sheets in peers:
        peer_path = tmp_path / (identity + ".xlsx")
        peer_content = _workbook(peer_sheets)
        peer_path.write_bytes(peer_content)
        peer = AuditInput(audit_id=identity, file_url=str(peer_path), customer="理想", project_id="project", file_metadata={"sha256": hashlib.sha256(peer_content).hexdigest()})
        by_type.setdefault(kind, []).append(peer)
        frozen.append(FrozenInput(identity, hashlib.sha256(peer_content).hexdigest(), "reference", peer_path.name))
    group = GroupContextInput(by_doc_type=by_type, by_audit_id={peer.audit_id: peer for values in by_type.values() for peer in values})
    target = FrozenTarget(audit.audit_id, "revision", suite.suite_key, suite.revision_digest, body, RuleBinding(suite.suite_key, checker_digest(suite.program_files), body["checker_binding"]))
    requests = []

    def observe(owner, fields, **_options):
        requests.append(dict(fields))
        if error:
            raise error
        values = {field: (visual or {}).get(field) for field in fields}
        for field, value in values.items():
            if value is not None:
                owner.witness({"kind": "xlsx_cell", "sheet": next(iter(sheets)), "cell": "B1"}, f"{field}={value}", visual=True)
        return values

    monkeypatch.setattr(SourceAudit, "visual_pdf", observe)
    opened = []
    context = SimpleNamespace(audit_file=audit, rule=rule, target=target, group_context=group, cache={}, on_close=opened.append, clause_id=clause)
    try:
        observed = check_factory(context, checker.create_audit).model_dump(mode="json")
        sources = (observed.get("evidence") or {}).get("sources", [])
        referenced = {audit.audit_id, *(json.loads(source["locator"])["file_version_id"] for source in sources)}
        observed["evidence_file_version_ids"] = sorted(referenced)
        checked = validate_results([observed], target=target, inputs=frozen)
        assert checked["coverage"] == [{"file_version_id": audit.audit_id, "clause_id": clause, "coverage": "mapped"}]
        result = checked["findings"][0]
        assert result["clause_id"] == clause
        if result["status"] == "PASS":
            assert result["evidence"]["sources"], result
        return result, requests
    finally:
        for opened_audit in reversed(opened):
            opened_audit.close()


def _native_case(entry, state):
    unknown = state == "unknown"
    good = state == "positive"
    formula = "=1+1"
    if entry == "L01":
        return "L01:4", {"全功能检验报告": [["供方检查方法/检查工具"], [formula if unknown else "千分尺" if good else "卡尺/千分尺"]]}
    if entry == "L02":
        return "L02:4", {"DVP&R": [["计划开始", "计划结束"], [formula if unknown else "2026-01-01" if good else "2026-01-02", "2026-01-02"]]}
    if entry == "L03":
        return "L03:2", {"试验报告": [["试验结论", formula if unknown else "pass" if good else "PASS"]]}
    if entry == "L04":
        return "L04:4", {"清单": [["零件号", "外协-国产", "外协-进口", "完成PPAP"], ["P123", "X", None, formula if unknown else "Y" if good else "y"]]}
    if entry == "L05":
        return "L05:2", {"测量系统分析计划表": [["序号", "新增业务列", "备注"], [1, formula if unknown else "已填" if good else None, None]]}
    if entry == "L06":
        return "L06:1", {"CCSC": [["工序流程编号", "P阶段Cpk结果"], ["10", formula if unknown else "1.34" if good else "1.33"]]}
    if entry == "L07":
        return "L07:2", {
            "PFMEA": [["PFMEA开始日期", formula if unknown else "2026-01-01" if good else "2025-12-31"], ["PFMEA修订日期", "2026-02-01"]],
            "变更履历": [["修订日期"], ["2026-01-01"], ["2026-02-01"]],
        }
    if entry == "L08":
        return "L08:2", {"控制计划": [["工序名称", "特殊特性分类"], ["压装", formula if unknown else "[G]" if good else "G"]]}
    if entry == "L09":
        return "L09:2", {"检具清单": [[formula if unknown else "W04项目检具清单" if good else "检具清单"]]}
    if entry == "L10":
        return "L10:3", {"模具验收报告": [["工艺类型", "模具设计尺寸"], ["注塑", formula if unknown else "100×50×30" if good else "100x50x30"]]}
    if entry == "L11":
        return "L11:4", {"过程流程图": [["工序名称", "操作描述"], ["压装", formula if unknown else "压入定位" if good else None]]}
    if entry == "L12":
        return "L12:3", {"CCSC": [["现有管理方式风险评估", "高风险管理项预防措施"], ["高风险", formula if unknown else "防错" if good else None]]}
    if entry == "L13":
        return "L13:2", {
            "设备开发验收清单": [["产线/工序", "设备验收", "验收结果", "整改措施", "整改计划"], ["10", "验收", "条件通过", formula if unknown else "整改" if good else None, "2027-01-01"]]
        }
    if entry == "L14":
        return "L14:4", {"产能评估表": [["线体/工序类别", "良率", "合格品数", "产出数量"], ["线体1", formula if unknown else 0.9 if good else 0.8, 9, 10]]}
    if entry == "L15":
        deadline = (date.today() + timedelta(days=30 if good else -1)).isoformat()
        return "L15:3", {"项目问题清单": [["问题描述", "对应措施", "时间节点", "负责人"], ["孔径偏差", "整改", formula if unknown else deadline, "SQE"]]}
    if entry == "L16":
        return "L16:4", {"表格": [["试验项目", "试验要求", "试验标准/方法", "样本数量"], ["耐久", "按规范", "GB1", formula if unknown else "3pcs" if good else "三件"]]}
    if entry == "L17":
        return "L17:1", {"生产件批准交付物文件清单": [["项目", "附件"], ["尺寸报告", formula if unknown else "报告.pdf" if good else None], ["零件认证相关", None]]}
    raise AssertionError(entry)


@pytest.fixture(autouse=True)
def forbid_uncontrolled_text_calls(monkeypatch):
    monkeypatch.setattr(SourceAudit, "text_result", lambda *args, **kwargs: pytest.fail("Live text model is forbidden in this owner test"))


@pytest.mark.parametrize("entry", [f"L{index:02}" for index in range(1, 18)])
@pytest.mark.parametrize(("state", "expected"), [("positive", "PASS"), ("negative", "NG"), ("unknown", "UNABLE")])
def test_each_leaf_has_a_real_native_positive_defect_and_unknown(tmp_path, monkeypatch, entry, state, expected):
    clause, sheets = _native_case(entry, state)
    result, calls = _execute(tmp_path, monkeypatch, entry, clause, sheets)
    assert result["status"] == expected, result
    assert not calls
    if result["evidence"]:
        assert all(json.loads(source["locator"])["file_version_id"] == "frozen-original" for source in result["evidence"]["sources"])


_VISUAL_CASES = [
    ("L01", "L01:2", {"importance_one": True}, {"importance_one": False}),
    ("L03", "L03:4", {"seal": True}, {"seal": False}),
    ("L03", "L03:5", {"before": True, "during": True, "after": True}, {"before": True, "during": True, "after": False}),
    ("L05", "L05:4", {"rr_accepted": True}, {"rr_accepted": False}),
    ("L08", "L08:1", {"production_selected": True}, {"production_selected": False}),
    ("L09", "L09:3", {"negative_attachment": True, "conclusion_filled": True}, {"negative_attachment": True, "conclusion_filled": False}),
    ("L10", "L10:4", {"physical_photo": True}, {"physical_photo": False}),
    ("L17", "L17:3", {"company_or_authorized_department": True}, {"company_or_authorized_department": False}),
    ("L17", "L17:5", {"authorized_signature": True, "seal": True}, {"authorized_signature": False, "seal": True}),
    ("L17", "L17:7", {"reason_selected": True}, {"reason_selected": False}),
    ("L17", "L17:8", {"approved_one": True, "submitted_all": True, "compliant_selected": True}, {"approved_one": True, "submitted_all": False, "compliant_selected": True}),
]


@pytest.mark.parametrize(("entry", "clause", "positive", "defect"), _VISUAL_CASES, ids=[case[1] for case in _VISUAL_CASES])
@pytest.mark.parametrize(("state", "expected"), [("positive", "PASS"), ("negative", "NG"), ("unknown", "UNABLE")])
def test_graphical_clauses_use_original_observations_not_printed_labels(tmp_path, monkeypatch, entry, clause, positive, defect, state, expected):
    sheets = {"量具重复性和再现性" if entry == "L05" else "原件": [["实际选择、签字、印章或照片区域"], ["提交原因", "□初始提交"]]}
    visual = positive if state == "positive" else defect if state == "negative" else {}
    result, calls = _execute(tmp_path, monkeypatch, entry, clause, sheets, visual=visual)
    assert result["status"] == expected, result
    assert len(calls) == 1
    assert set(calls[0]) == set(positive)


def _inventory():
    rows = []
    for suite in load_predefined_checkers():
        if not suite.source_path.startswith("v5/理想/"):
            continue
        namespace = {}
        exec(suite.files["resources.py"], namespace)
        resource = namespace["RESOURCE"]
        rows.append((suite, resource))
    return rows


@pytest.mark.parametrize(("entry", "clause", "gap"), [(suite.source_entry, clause, gap) for suite, resource in _inventory() for clause, gap in resource["known_gaps"].items()])
def test_every_declared_gap_is_explicit_unable_not_a_factory_error(tmp_path, monkeypatch, entry, clause, gap):
    result, calls = _execute(tmp_path, monkeypatch, entry, clause, {"原件": [["原件数据不能代替尚未实现的取证范围"]]})
    assert result["status"] == "UNABLE", result
    assert result["checks_performed"] == 0
    assert gap in result["suggestions"]
    assert not calls


@pytest.mark.parametrize(("entry", "clause"), [(suite.source_entry, clause) for suite, resource in _inventory() for clause in resource["clauses"]])
def test_unsupported_carrier_cannot_execute_a_declared_reader(tmp_path, monkeypatch, entry, clause):
    result, calls = _execute(tmp_path, monkeypatch, entry, clause, {"原件": [["有内容也不是支持的载体"]]}, suffix=".txt")
    assert result["status"] == "UNABLE", result
    assert result["checks_performed"] == 0
    assert not calls


def test_each_leaf_declares_exact_clauses_scope_and_known_gaps_without_version_imports():
    import ast

    from audit.checker_lib.imports import import_violations

    inventory = _inventory()
    assert len(inventory) == 17
    readers = 0
    for suite, resource in inventory:
        bound = set(suite.entrypoints)
        assert resource["customer"] == "理想"
        assert resource["clauses"]
        assert set(resource["clauses"]) | set(resource["known_gaps"]) == bound
        assert set(resource["clauses"]).isdisjoint(resource["known_gaps"])
        source = suite.files["checker.py"].decode()
        assert not import_violations(ast.parse(source))
        assert not import_violations(ast.parse(suite.files["resources.py"]))
        for item in resource["clauses"].values():
            assert item["scope"] and item["carriers"]
            assert f"def {item['method']}(" in source
        readers += len(resource["clauses"])
    assert readers == 70


def _fields(*names):
    return [[name, f"实际内容-{index}"] for index, name in enumerate(names, 1)]


def _msa_measurements():
    rows = [[None] * 12 for _ in range(20)]
    rows[0][0] = "量具重复性和再现性"
    rows[6][2:12] = list(range(1, 11))
    for evaluator, first in (("A", 8), ("B", 13), ("C", 18)):
        rows[first][0] = evaluator
        for row in range(first - 1, first + 2):
            rows[row][1] = row - first + 2
            rows[row][2:12] = [0] * 10
    return {"量具重复性和再现性": rows}


_ADDITIONAL_POSITIVES = [
    ("L17", "L17:2", {"PSW": [["重量(kg)", "1.2300"]]}),
    ("L04", "L04:1", {"材料清单": [["零件号", "单件重量"], ["P123", "1.2300"]]}),
    ("L05", "L05:3", _msa_measurements()),
    ("L01", "L01:1", {"全功能检验报告": [["全功能检验报告"]]}),
    ("L01", "L01:8", {"全功能检验报告": _fields("车型", "零件名称", "零件号", "供应商", "责任人", "日期")}),
    ("L02", "L02:1", {"DVP&R": _fields("版本号", "修订日期", "修订部门/姓名", "零件/总成名称", "适用车型", "零件/总成编号", "供应商名称")}),
    ("L02", "L02:5", {"DVP&R": [["试验实际开始日期", "试验实际结束日期"], ["2026-01-01", "2026-01-02"]]}),
    ("L03", "L03:1", {"试验报告": [["报告编号", "T123"]]}),
    ("L03", "L03:3", {"试验报告": [["试验类型", "pv"]]}),
    ("L03", "L03:6", {"试验报告": [["样本数量", 3]]}),
    (
        "L04",
        "L04:2",
        {
            "材料清单": [
                ["零件号", "材料执行标准", "材料类型", "材料牌号", "材料规格/尺寸", "材料供应商名称（包括生产地点）", "通过何种质量管理体系认证"],
                ["P123", "GB1", "钢", "Q1", "10mm", "供方A", "ISO9001"],
            ]
        },
    ),
    ("L04", "L04:3", {"材料清单": [["零件号", "自制", "外协-国产", "外协-进口", "领用件"], ["P123", "X", None, None, None]]}),
    ("L05", "L05:1", {"MSA": [["任务项一", "OK"], ["任务项二", "OK"]]}),
    ("L06", "L06:2", {"CCSC": [["工序流程编号", "P阶段结果判定"], ["10", "达标"]]}),
    ("L06", "L06:3", {name: [["短期过程能力", "研究"], ["Cpk", 1.34]] for name in ("I-MR", "Xbar-R", "Xbar-S")}),
    ("L07", "L07:1", {"PFMEA": _fields("公司名称", "车型项目", "零件名称", "零件号", "制造地址", "PFMEA开始日期", "PFMEA修订日期", "跨职能团队")}),
    ("L08", "L08:4", {"控制计划": [["工序名称", "特殊特性分类", "控制方法"], ["压装", "[G]", "SPC"]]}),
    ("L08", "L08:5", {"控制计划": [["工序名称", "频率"], ["压装", "每班"]]}),
    ("L10", "L10:1", {"模具验收报告": [["零件供应商名称", "供应商甲"], ["提交日期", "2026-01-01"]]}),
    (
        "L10",
        "L10:2",
        {
            "模具验收报告": [
                [
                    "工艺类型",
                    "一级分总成零件名称",
                    "分总成组件或分序模具名称",
                    "模具制造供应商",
                    "模具存放地点",
                    "零件质量评估",
                    "模具分供方及实物VS 数据&标准验收",
                    "模具量产性能评估",
                    "模具维保评估",
                ],
                ["注塑", "组件", "模具A", "供方甲", "工厂A", "合格", "合格", "合格", "合格"],
            ]
        },
    ),
    ("L10", "L10:5", {"模具验收报告": [["编制", "张三"], ["审核", "李四"]]}),
    ("L10", "L10:6", {"模具验收报告": [["工艺类型", "实物确认"], ["注塑", "有"]]}),
    ("L11", "L11:1", {"过程流程图": _fields("零件号", "零件名称", "项目车型", "供应商名称", "代码", "编制日期", "版本", "团队")}),
    ("L11", "L11:5", {"过程流程图": [["工序名称", "制造", "运输", "存储", "检验", "返工", "报废"], ["压装", "●", None, None, None, None, None]]}),
    ("L12", "L12:1", {"CCSC": _fields("零件名称", "零件号", "供应商名称", "供应商编码")}),
    (
        "L13",
        "L13:1",
        {
            "设备开发验收清单": [
                ["产线/工序", "设备开发", "设备", "供应商", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间"],
                ["10", "开发", "压装机", "设备供应商", "M1", 0, 1, "专用", "2027-01-01"],
            ]
        },
    ),
    ("L14", "L14:1", {"产能评估表": _fields("零件名称", "零件号", "供应商名称", "供应商编码", "产能评估总体结论")}),
    ("L14", "L14:2", {"产能评估表": [["线体/工序类别", "产出数量"], ["压装", 20]]}),
    ("L14", "L14:3", {"产能评估表": [["产能评估总体结论", "条件通过"], ["后续措施", "增加工位"], ["SOP+1达成风险", "SOP+2达成风险", "SOP+3达成风险"], ["低风险", "中风险", "低风险"]]}),
    ("L14", "L14:5", {"产能评估表": [["单线体/工序总产出JPH", "产出数量", "净利用时间", "参与测试线体数量"], [5, 20, 2, 2]]}),
    ("L14", "L14:6", {"产能评估表": [["线体/工序预估月度产能", "单线体/工序总产出JPH", "现有线体数", "班次/天", "每班净利用时间", "每月标准工作天数", "累积良率"], [1440, 5, 2, 1, 8, 20, "90%"]]}),
    ("L15", "L15:1", {"项目问题清单": _fields("车型", "供应商名称", "零件编号", "零件名称")}),
    ("L15", "L15:2", {"项目问题清单": [["过程审核问题是否完全关闭？", "Yes"], ["检验体系审核问题是否完全关闭？", "Yes"], ["供应商追溯体系评审问题是否完全关闭？", "Yes"], ["其他专项问题", "Yes"]]}),
    ("L15", "L15:4", {"项目问题清单": [["问题描述", "整车/总成功/性能性、SOP+3产能风险、安全性、法律法规风险"], ["孔径偏差", "Yes"]]}),
    ("L16", "L16:1", {"年度试验": [["质量等级"], ["一般件"]]}),
    ("L16", "L16:2", {"年度试验": [["试验项目", "测试频率"], ["ELV试验", "每两年"], ["耐久", "每年"]]}),
    ("L16", "L16:3", {"年度试验": [["供应商名称"], ["S01-供应商全称"]]}),
    ("L16", "L16:5", {"年度试验": [["试验项目", "试验要求", "试验标准/方法"], ["耐久", "符合规范", "GB1"]]}),
    ("L16", "L16:6", {"年度试验": [["车型"], ["M1"]]}),
    (
        "L17",
        "L17:6",
        {
            "零件提交保证书": _fields(
                "零件名称",
                "理想汽车零件号",
                "供方料号",
                "图纸号",
                "模具穴号",
                "工程更改版本",
                "版本日期",
                "重量(kg)",
                "供应商名称/供应商代码",
                "街道地址",
                "城市",
                "省",
                "国家",
                "顾客名称",
                "设计开发",
                "采购工程",
                "PTC",
            )
        },
    ),
]


@pytest.mark.parametrize(("entry", "clause", "sheets"), _ADDITIONAL_POSITIVES, ids=[case[1] for case in _ADDITIONAL_POSITIVES])
def test_remaining_native_readers_have_source_backed_positive_examples(tmp_path, monkeypatch, entry, clause, sheets):
    result, calls = _execute(tmp_path, monkeypatch, entry, clause, sheets)
    assert result["status"] == "PASS", result
    assert not calls


@pytest.mark.parametrize(("status", "expected"), [("PASS", "PASS"), ("NG", "NG"), ("UNABLE", "UNABLE")])
def test_pfmea_meaning_uses_text_with_native_source_not_visual_transcription(tmp_path, monkeypatch, status, expected):
    observed = []

    def text(owner, fields, description):
        observed.append((fields, description))
        owner.witness({"kind": "xlsx_cell", "sheet": "PFMEA", "cell": "A2"}, "内部：安装；客户工厂：装车；最终用户：乘坐")
        return status, "受控文本观察"

    monkeypatch.setattr(SourceAudit, "text_result", text)
    result, calls = _execute(tmp_path, monkeypatch, "L07", "L07:4", {"PFMEA": [["过程项功能"], ["内部：安装；客户工厂：装车；最终用户：乘坐"]]})
    assert result["status"] == expected, result
    assert len(observed) == 1 and not calls


@pytest.mark.parametrize(
    ("facts", "expected"), [({"operation_shapes": True, "connections": True}, "PASS"), ({"operation_shapes": True, "connections": False}, "NG"), ({"operation_shapes": True}, "UNABLE")]
)
def test_flow_connections_are_actual_graphical_evidence(tmp_path, monkeypatch, facts, expected):
    sheets = {"过程流程图": [["工序名称", "制造", "运输", "存储", "检验", "返工", "报废"], ["压装", "●", None, None, None, None, None]]}
    result, calls = _execute(tmp_path, monkeypatch, "L11", "L11:6", sheets, visual=facts)
    assert result["status"] == expected, result
    assert len(calls) == 1


def test_header_names_cannot_count_as_metadata_values(tmp_path, monkeypatch):
    rows = [["版本号", "修订日期", "修订部门/姓名", "零件/总成名称", "适用车型", "零件/总成编号", "供应商名称", "其他标签"]]
    result, calls = _execute(tmp_path, monkeypatch, "L02", "L02:1", {"DVP&R": rows})
    assert result["status"] == "UNABLE", result
    assert not calls


@pytest.mark.parametrize(
    ("entry", "clause", "title", "rows", "expected"),
    [
        ("L06", "L06:1", "CCSC", [["工序流程编号", "P阶段Cpk结果"], ["10", 1.34], ["20", None]], "UNABLE"),
        ("L08", "L08:2", "控制计划", [["工序名称", "特殊特性分类"], ["压装", "[G]"], ["焊接", None]], "NG"),
        ("L08", "L08:5", "控制计划", [["工序名称", "频率"], ["压装", "每班"], ["焊接", None]], "NG"),
        ("L16", "L16:4", "年度试验", [["试验项目", "试验要求", "样本数量"], ["耐久", "规范", "3pcs"], [None, "规范", None]], "NG"),
        ("L16", "L16:5", "年度试验", [["试验项目", "试验要求", "试验标准/方法"], ["耐久", "规范", "GB1"], [None, "规范", "GB2"]], "NG"),
    ],
)
def test_blank_selected_cells_do_not_remove_physical_business_rows(tmp_path, monkeypatch, entry, clause, title, rows, expected):
    result, calls = _execute(tmp_path, monkeypatch, entry, clause, {title: rows})
    assert result["status"] == expected, result
    assert not calls


def test_negative_after_native_blank_gap_still_executes(tmp_path, monkeypatch):
    rows = [["试验项目", "试验要求", "试验标准/方法"], ["耐久", "规范", "GB1"], *[[None, None, None] for _ in range(8)], ["疲劳", None, "GB2"]]
    result, calls = _execute(tmp_path, monkeypatch, "L16", "L16:5", {"年度试验": rows})
    assert result["status"] == "NG", result
    assert not calls


def test_native_pdf_table_uses_excel_preparation_and_original_pdf_locations(tmp_path, monkeypatch):
    import fitz

    with fitz.open() as document:
        page = document.new_page(width=460, height=270)
        for y in (20, 70, 120, 170, 220):
            page.draw_line((20, y), (440, y))
        for x in (20, 240, 440):
            page.draw_line((x, 20), (x, 220))
        for x, y, text in ((40, 50, "DV报告"), (40, 100, "报告编号"), (260, 100, "T123"), (40, 150, "试验结论"), (260, 150, "pass"), (40, 200, "试验类型"), (260, 200, "PV")):
            page.insert_text((x, y), text, fontname="china-s", fontsize=11)
        content = document.tobytes()
    result, calls = _execute(tmp_path, monkeypatch, "L03", "L03:2", {}, content=content, suffix=".pdf")
    assert result["status"] == "PASS", result
    assert result["evidence"]["source_format"] == "pdf"
    assert result["evidence"]["prepared_format"] == "xlsx"
    assert not calls
    for source in result["evidence"]["sources"]:
        locator = json.loads(source["locator"])
        assert locator["kind"] == "pdf_cell"
        assert locator["page"] == 1 and locator["bbox"]
        assert locator["file_version_id"] == "frozen-original"
        assert locator["source_sha256"] == hashlib.sha256(content).hexdigest()


def test_native_source_digest_change_is_err_before_business_pass(tmp_path, monkeypatch):
    clause, sheets = _native_case("L03", "positive")
    result, calls = _execute(tmp_path, monkeypatch, "L03", clause, sheets, expected_sha="0" * 64)
    assert result["status"] == "ERR", result
    assert result["checks_performed"] == 0 and not calls


def test_declared_native_positive_cases_cover_each_reader():
    expected = {clause for _suite, resource in _inventory() for clause in resource["clauses"]}
    covered = {_native_case(entry, "positive")[0] for entry in (f"L{index:02}" for index in range(1, 18))}
    covered.update(case[1] for case in _VISUAL_CASES)
    covered.update(case[1] for case in _ADDITIONAL_POSITIVES)
    covered.update({"L07:4", "L11:6"})
    assert covered == expected


@pytest.mark.parametrize("quantity", [0, "0pcs", "３件", "٣pcs"])
def test_sample_digits_do_not_add_a_positive_minimum(tmp_path, monkeypatch, quantity):
    result, _calls = _execute(tmp_path, monkeypatch, "L16", "L16:4", {"年度试验": [["样本数量"], [quantity]]})
    assert result["status"] == "PASS", result


@pytest.mark.parametrize("entry", [f"L{index:02}" for index in range(1, 18)])
def test_leaf_runtime_cannot_import_versioned_business_modules(tmp_path, monkeypatch, entry):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "audit.checkers" or name.startswith("audit.checkers."):
            pytest.fail("Directory leaf tried to import versioned business code: " + name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    clause, sheets = _native_case(entry, "positive")
    result, _calls = _execute(tmp_path, monkeypatch, entry, clause, sheets)
    assert result["status"] == "PASS", result


@pytest.mark.parametrize(("clause", "label", "neighbor"), [("L03:1", "报告编号", "试验结论"), ("L03:2", "试验结论", "试验类型"), ("L03:3", "试验类型", "样品数量"), ("L03:6", "样品数量", "报告编号")])
def test_untamplated_report_neighboring_labels_are_not_business_values(tmp_path, monkeypatch, clause, label, neighbor):
    result, calls = _execute(tmp_path, monkeypatch, "L03", clause, {"试验报告": [[label, neighbor]]})
    assert result["status"] == "UNABLE", result
    assert not calls


@pytest.mark.parametrize(("risk", "expected"), [("待确认", "UNABLE"), ("中风险（待复核）", "UNABLE"), (None, "UNABLE"), ("无风险", "PASS"), ("低风险", "PASS"), ("中风险", "NG"), ("高风险", "NG")])
def test_capacity_unknown_risk_does_not_prove_condition_is_absent(tmp_path, monkeypatch, risk, expected):
    sheets = {"产能评估表": [["产能评估总体结论", "通过"], ["后续措施", "按当前计划"], ["线体/工序类别", "SOP+1达成风险", "SOP+2达成风险", "SOP+3达成风险"], ["线体1", risk, "低风险", "无风险"]]}
    result, calls = _execute(tmp_path, monkeypatch, "L14", "L14:3", sheets)
    assert result["status"] == expected, result
    assert not calls


def test_capacity_blank_risk_row_with_line_identity_cannot_disappear(tmp_path, monkeypatch):
    rows = [["线体/工序类别", "SOP+1达成风险", "SOP+2达成风险", "SOP+3达成风险"], ["线体1", "低风险", "低风险", "低风险"], ["线体2", None, None, None]]
    result, calls = _execute(tmp_path, monkeypatch, "L14", "L14:3", {"产能评估表": rows})
    assert result["status"] == "UNABLE", result
    assert not calls


@pytest.mark.parametrize("verdict", ["待确认", "UNKNOWN", "???"])
def test_equipment_unknown_acceptance_is_not_unconditional_pass(tmp_path, monkeypatch, verdict):
    rows = [["产线/工序", "设备验收", "验收结果", "整改措施", "整改计划"], ["产线A", None, verdict, None, None]]
    result, calls = _execute(tmp_path, monkeypatch, "L13", "L13:2", {"设备开发验收清单": rows})
    assert result["status"] == "UNABLE", result
    assert result["checks_performed"] == 0 and not calls

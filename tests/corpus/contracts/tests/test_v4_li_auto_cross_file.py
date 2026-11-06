"""Li Auto source binding and two-original facts through the actual Round."""

import io

import pytest
from audit.audits import RoundInput, RoundWork
from audit.checkers.v4.pfmea_local import pair_identity
from audit.presets import preset_rule, preset_rule_body
from corpus_contracts.core_round import customer_suite, run_suite_round
from openpyxl import Workbook, load_workbook


def serialized(book):
    content = io.BytesIO()
    book.save(content)
    book.close()
    return content.getvalue()


def pfmea(*, characteristic="G", process="10 成型", severity=6, detection=6, function=None, part="P123", revision="A"):
    book = Workbook()
    sheet = book.active
    sheet.title = "PFMEA"
    for label, value in [
        ("公司名称", "供应商"),
        ("车型项目", "X1"),
        ("零件名称", "支架"),
        ("零件号", part),
        ("版本", revision),
        ("客户名称", "理想"),
        ("制造地址", "上海"),
        ("PFMEA开始日期", "2026-01-01"),
        ("PFMEA修订日期", "2026-02-01"),
        ("跨职能团队", "质量/制造"),
    ]:
        sheet.append([label, value])
    sheet.append(["过程步骤", "特殊特性", "过程项功能", "失效影响", "严重度", "现行探测控制", "探测度"])
    sheet.append(
        [
            process,
            characteristic,
            function or "内部：压装\n客户工厂：装车\n最终用户：乘坐",
            "内部：返工（4）\n客户工厂：返工（6）\n最终用户：异响（5）",
            severity,
            "双感官检查（6）\n目视检查（8）",
            detection,
        ]
    )
    history = book.create_sheet("变更履历")
    history.append(["修订日期"])
    history.append(["2026-01-01"])
    history.append(["2026-02-01"])
    return serialized(book)


def pfd(*, part="P123", revision="A", customer="理想", process="成型", mark="【G】", generic=False):
    book = Workbook()
    sheet = book.active
    sheet.title = "过程流程图"
    sheet.append(["零件号", part, "版本", revision])
    sheet.append(["客户名称", customer])
    sheet.append(["工序号", "工序名称", "产品特殊特性", "特性类别" if generic else "产品特性类别", "过程特殊特性", "特性类别" if generic else "过程特性类别"])
    sheet.append([10, process, "孔径", mark, "温度", "【Z】"])
    return serialized(book)


def execute(entry, content, references, *, reverse=False):
    body = preset_rule_body("理想", entry)
    if reverse:
        body["rules"].reverse()
    target = RoundInput("target", "file", "version", "原件.xlsx", "ready", rule_version_id="li-rule", rule_body=body)
    inputs = [target]
    blobs = {"version": content}
    reference_types = {}
    for index, (name, ref) in enumerate(references):
        version = f"ref-{index}"
        reference_types[version] = "过程流程图" if entry == "L07" else "零件提交保证书" if name == "交付清单.xlsx" else "控制计划" if name == "控制计划.xlsx" else "过程流程图"
        inputs.append(RoundInput("reference", version, version, name, "ready"))
        if ref is not None:
            blobs[version] = ref
    result = run_suite_round(RoundWork("round", "tenant", inputs), blobs, suites={"version": customer_suite("理想", entry)}, document_types=reference_types)
    return {row["item_key"].split(":rule:", 1)[1]: row for row in result["findings"]}, result


def test_all_seven_original_pfmea_clauses_and_two_originals():
    findings, result = execute("L07", pfmea(), [("过程流程图.xlsx", pfd())])
    clauses = preset_rule("理想", "L07").clauses
    assert [findings[c.id]["status"] for c in clauses] == ["PASS"] * 7
    assert all(row["coverage"] == "executed" for row in result["coverage"]["rule_items"])
    for index in (2, 4):
        evidence = findings[clauses[index].id]["locator"]["semantic"]["sources"]
        assert {row["file_version_id"] for row in evidence} == {"version", "ref-0"}


@pytest.mark.parametrize(("changes", "index"), [({"function": "只有内部"}, 3), ({"severity": 5}, 5), ({"detection": 8}, 6), ({"characteristic": "CC"}, 4), ({"process": "10 检查"}, 2)])
def test_source_id_binds_the_equivalent_check_even_when_not_same_ordinal(changes, index):
    findings, _ = execute("L07", pfmea(**changes), [("过程流程图.xlsx", pfd())], reverse=True)
    assert findings[preset_rule("理想", "L07").clauses[index].id]["status"] == "NG"


@pytest.mark.parametrize(
    "references",
    [[], [("过程流程图.xlsx", pfd(part="OTHER"))], [("过程流程图.xlsx", pfd(revision="B"))], [("过程流程图.xlsx", pfd(customer="蔚来"))], [("过程流程图.xlsx", pfd()), ("过程流程图2.xlsx", pfd())]],
)
def test_pairing_uncertainty_is_not_a_confirmed_defect(references):
    findings, _ = execute("L07", pfmea(), references)
    clauses = preset_rule("理想", "L07").clauses
    assert [findings[clauses[i].id]["status"] for i in (2, 4)] == ["UNABLE", "UNABLE"]


def test_frozen_but_unreadable_peer_is_a_technical_failure():
    findings, _ = execute("L07", pfmea(), [("过程流程图.xlsx", None)])
    clauses = preset_rule("理想", "L07").clauses
    assert [findings[clauses[i].id]["status"] for i in (2, 4)] == ["ERR", "ERR"]


def warranty(*, part="P123", revision="A"):
    book = Workbook()
    sheet = book.active
    sheet.title = "零件提交保证书"
    sheet.append(["理想汽车零件号", part, "工程更改版本", revision])
    sheet.append(["客户名称", "理想"])
    return serialized(book)


def delivery(*, part="P123", customer="理想", revision="A", attachment="尺寸报告.pdf"):
    book = Workbook()
    sheet = book.active
    sheet.title = "生产件批准交付物文件清单"
    sheet.append(["零件号", part, "版本", revision])
    sheet.append(["客户名称", customer])
    sheet.append(["项目", "附件"])
    sheet.append(["尺寸报告", attachment])
    sheet.append(["零件认证相关", None])
    return serialized(book)


def test_warranty_uses_frozen_independent_delivery_list_with_two_original_locations():
    findings, _ = execute("L17", warranty(), [("交付清单.xlsx", delivery())])
    row = findings[preset_rule("理想", "L17").clauses[0].id]
    assert row["status"] == "PASS", row
    sources = row["locator"]["semantic"]["sources"]
    assert {source["file_version_id"] for source in sources} == {"version", "ref-0"}


@pytest.mark.parametrize(("changes", "expected"), [({"attachment": None}, "NG"), ({"part": "OTHER"}, "UNABLE"), ({"customer": "蔚来"}, "UNABLE"), ({"revision": "B"}, "UNABLE")])
def test_warranty_pair_defects_are_separate_from_uncertain_pairing(changes, expected):
    findings, _ = execute("L17", warranty(), [("交付清单.xlsx", delivery(**changes))])
    assert findings[preset_rule("理想", "L17").clauses[0].id]["status"] == expected


def test_warranty_unreadable_frozen_list_is_error():
    findings, _ = execute("L17", warranty(), [("交付清单.xlsx", None)])
    assert findings[preset_rule("理想", "L17").clauses[0].id]["status"] == "ERR"


@pytest.mark.parametrize("kind", ["控制计划", "过程流程图"])
def test_warranty_readable_delivery_list_does_not_depend_on_unrelated_failed_inputs(kind):
    findings, _ = execute("L17", warranty(), [("交付清单.xlsx", delivery()), (kind + ".xlsx", None)])
    assert findings[preset_rule("理想", "L17").clauses[0].id]["status"] == "PASS"


@pytest.mark.parametrize("field,value", [("part", "无"), ("part", "/"), ("part", "TBD"), ("part", "零件名称"), ("revision", "无"), ("revision", "版本号")])
def test_li_auto_pair_does_not_treat_matching_placeholders_as_identity(field, value):
    values = {field: value}
    findings, _ = execute("L17", warranty(**values), [("交付清单.xlsx", delivery(**values))])
    assert findings[preset_rule("理想", "L17").clauses[0].id]["status"] == "UNABLE"
    findings, _ = execute("L07", pfmea(**values), [("过程流程图.xlsx", pfd(**values))])
    assert [findings[preset_rule("理想", "L07").clauses[index].id]["status"] for index in (2, 4)] == ["UNABLE", "UNABLE"]


def test_pfmea_generic_category_headers_keep_original_text_in_evidence():
    findings, _ = execute("L07", pfmea(), [("过程流程图.xlsx", pfd(generic=True))])
    row = findings[preset_rule("理想", "L07").clauses[4].id]
    assert row["status"] == "PASS", row
    assert any(source["excerpt"] == "特性类别" for source in row["locator"]["semantic"]["sources"])


@pytest.mark.parametrize("part", ["P-123", "P/123", "P.123", "000123"])
def test_pair_identity_preserves_real_part_punctuation_and_leading_zeros(part):
    assert pair_identity(part) == part.casefold()
    findings, _ = execute("L17", warranty(part=part, revision=None), [("交付清单.xlsx", delivery(part=part, revision=None))])
    assert findings[preset_rule("理想", "L17").clauses[0].id]["status"] == "PASS"


@pytest.mark.parametrize("label", ["零件号：", "项目代号", "UAES项目编号", "工程更改版本", "Part No."])
def test_pair_identity_rejects_copied_source_labels(label):
    assert pair_identity(label) is None


def embedded_warranty(*, main_part="P123", main_revision="A", main_project="X1", metadata=None, attachment="尺寸报告.pdf"):
    book = load_workbook(io.BytesIO(warranty(part=main_part, revision=main_revision)))
    book.active.append(["车型项目", main_project])
    sheet = book.create_sheet("生产件批准交付物文件清单")
    for name, value in (metadata or {}).items():
        sheet.append([name, value])
    sheet.append(["项目", "附件"])
    sheet.append(["尺寸报告", attachment])
    return serialized(book)


@pytest.mark.parametrize(
    "metadata",
    [{"零件号": "OTHER"}, {"零件号": "无"}, {"零件号": "零件名称"}, {"版本": "B"}, {"版本": "版本号"}, {"版本": "无"}, {"车型项目": "X2"}, {"车型项目": "待定"}, {"零件号": None}, {"版本": None}],
)
def test_embedded_delivery_cannot_override_warranty_identity(metadata):
    findings, _ = execute("L17", embedded_warranty(metadata=metadata), [])
    assert findings[preset_rule("理想", "L17").clauses[0].id]["status"] == "UNABLE"


@pytest.mark.parametrize("metadata", [None, {"零件号": "P123", "版本": "A", "车型项目": "X1"}])
def test_embedded_delivery_inherits_identity_only_when_not_separately_declared(metadata):
    findings, _ = execute("L17", embedded_warranty(metadata=metadata), [])
    assert findings[preset_rule("理想", "L17").clauses[0].id]["status"] == "PASS"


def test_embedded_delivery_missing_attachment_is_still_defect():
    findings, _ = execute("L17", embedded_warranty(attachment=None), [])
    assert findings[preset_rule("理想", "L17").clauses[0].id]["status"] == "NG"


@pytest.mark.parametrize("attachment,expected", [("尺寸报告.pdf", "PASS"), (None, "NG")])
def test_delivery_only_original_checks_attachments_without_invented_warrant_requirement(attachment, expected):
    book = Workbook()
    sheet = book.active
    sheet.title = "生产件批准交付物文件清单"
    sheet.append(["项目", "附件"])
    sheet.append(["尺寸报告", attachment])
    findings, _ = execute("L17", serialized(book), [])
    assert findings[preset_rule("理想", "L17").clauses[0].id]["status"] == expected


@pytest.mark.parametrize(
    "label,value",
    [("零件号", "OTHER"), ("版本", "B"), ("车型项目", "X2"), ("零件号", None), ("零件号", "=1+1")],
)
def test_embedded_identity_after_row_40_is_not_treated_as_undeclared(label, value):
    book = load_workbook(io.BytesIO(embedded_warranty()))
    sheet = book["生产件批准交付物文件清单"]
    sheet.cell(41, 1, label)
    if value is not None:
        sheet.cell(41, 2, value)
    findings, _ = execute("L17", serialized(book), [])
    assert findings[preset_rule("理想", "L17").clauses[0].id]["status"] == "UNABLE"


@pytest.mark.parametrize("declared", [False, True])
def test_embedded_identity_scope_past_row_40_preserves_valid_inheritance(declared):
    book = load_workbook(io.BytesIO(embedded_warranty()))
    sheet = book["生产件批准交付物文件清单"]
    sheet.cell(41, 1, "零件号" if declared else "补充交付物")
    sheet.cell(41, 2, "P123" if declared else "附件.pdf")
    findings, _ = execute("L17", serialized(book), [])
    assert findings[preset_rule("理想", "L17").clauses[0].id]["status"] == "PASS"

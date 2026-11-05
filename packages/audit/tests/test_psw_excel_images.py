"""Embedded Excel images reach the existing V2/V3 seal and signature checks."""

from io import BytesIO

import pytest
from audit.checkers.v2.psw_excel_agent import PswExcelAgent as V2PswAgent
from audit.checkers.v3.psw_excel_agent import PswExcelAgent as V3PswAgent
from audit.contracts import AuditInput, RuleInput
from audit.engine.runtime import bind_runtime
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as WorkbookImage
from PIL import Image, ImageDraw


@pytest.mark.parametrize(("profile", "agent_type"), [("v2", V2PswAgent), ("v3", V3PswAgent)], ids=["v2", "v3"])
@pytest.mark.parametrize(
    ("image_kind", "authorized_signature", "seal_status", "signature_status"),
    [
        ("center-stamp", None, "PASS", "PASS"),
        ("corner-stamp", None, "PASS", "NG"),
        ("corner-stamp", "王工", "PASS", "PASS"),
        ("blank", "王工", "NG", "NG"),
        ("blank", None, "NG", "NG"),
        ("none", None, "NG", "NG"),
    ],
    ids=["detected-seal-and-signature", "seal-without-signature", "seal-and-authorized-signature", "signature-without-seal", "blank-image", "no-image"],
)
def test_embedded_images_and_authorized_signature_preserve_existing_excel_decisions(tmp_path, profile, agent_type, image_kind, authorized_signature, seal_status, signature_status):
    path = tmp_path / "psw.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "零件提交保证书"
    sheet["A25"] = "经授权的供方代表签字"
    sheet["B25"] = authorized_signature
    if image_kind != "none":
        image = Image.new("RGB", (300, 300), "white")
        if image_kind != "blank":
            bounds = (100, 100, 230, 230) if image_kind == "center-stamp" else (25, 25, 155, 155)
            ImageDraw.Draw(image).ellipse(bounds, outline=(220, 0, 0), width=18)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        sheet.add_image(WorkbookImage(buffer), "D25")
    workbook.save(path)
    workbook.close()
    agent = agent_type(
        AuditInput(audit_id="psw-images", file_url=str(path)),
        RuleInput(
            rule_id="confirmed-psw-rules",
            rule_profile=profile,
            type="零件提交保证书",
            rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 11)],
        ),
    )

    try:
        with bind_runtime(None):
            seal = agent.check(2)
            signature = agent.check(4)
        assert seal is not None and signature is not None
        assert seal.status == seal_status
        assert signature.status == signature_status
        if seal_status == "PASS":
            assert seal.suggestions == ""
            assert seal.evidence.used == "visual"
            assert [(source.kind, source.locator) for source in seal.evidence.sources] == [("visual", "workbook://embedded-images")]
        else:
            assert "未检测到有效印章" in seal.suggestions
        if signature_status == "PASS":
            assert signature.suggestions == ""
            assert signature.evidence.used == "both"
            assert [(source.kind, source.locator) for source in signature.evidence.sources] == [("structure", "workbook://structured-cells"), ("visual", "workbook://embedded-images")]
        elif image_kind == "corner-stamp":
            assert "缺少有效经授权签字" in signature.suggestions
        else:
            assert "盖章" in signature.suggestions
    finally:
        agent.close()


@pytest.mark.parametrize(("profile", "agent_type"), [("v2", V2PswAgent), ("v3", V3PswAgent)], ids=["v2", "v3"])
@pytest.mark.parametrize("missing,expected", [(True, "NG"), (False, "UNABLE")])
def test_required_field_defect_is_preserved_when_later_field_is_unknown(tmp_path, profile, agent_type, missing, expected):
    path = tmp_path / "required-fields.xlsx"
    native_psw_fields(path, unknown=("零件提交保证书", "B19"))
    if missing:
        workbook = load_workbook(path)
        workbook["零件提交保证书"]["B8"] = None
        workbook.save(path)
        workbook.close()
    agent = agent_type(AuditInput(audit_id="native-partial", file_url=str(path)), RuleInput(rule_id="required", rule_profile=profile, type="零件提交保证书", rules=[]))
    try:
        with bind_runtime(None):
            result = agent.check(5)
        assert result.status == expected
        if missing:
            assert "图纸号" in result.suggestions
        assert "#N/A" not in result.suggestions
    finally:
        agent.close()


@pytest.mark.parametrize(("profile", "agent_type"), [("v2", V2PswAgent), ("v3", V3PswAgent)], ids=["v2", "v3"])
def test_corrupt_native_psw_workbook_is_an_execution_error_without_invented_evidence(tmp_path, profile, agent_type):
    path = tmp_path / "broken.xlsx"
    original = b"not a readable workbook"
    path.write_bytes(original)
    agent = agent_type(
        AuditInput(audit_id="psw-corrupt", file_url=str(path)),
        RuleInput(rule_id="confirmed-psw-rules", rule_profile=profile, type="零件提交保证书", rules=[]),
    )

    try:
        with bind_runtime(None):
            result = agent.check(2)
        assert result.status == "ERR"
        assert result.evidence.used == "none"
        assert result.evidence.sources == ()
        assert [(row.code, row.stage, row.severity) for row in result.evidence.diagnostics] == [("psw.evaluation.runtime_error", "evaluation", "error")]
        assert path.read_bytes() == original
    finally:
        agent.close()


def native_psw_fields(path, *, unknown=None):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "零件提交保证书"
    sheet["A1"] = "零件提交保证书"
    sheet["A2"] = "供应商名称/供应商代码"
    sheet["C2"] = "顾客名称"
    sheet["A3"] = "示例有限公司/S001"
    sheet["C3"] = "理想汽车"
    fields = (
        ("重量(kg)", "1.2345"),
        ("零件名称", "样件"),
        ("理想汽车零件号", "P001"),
        ("供方料号", "S001"),
        ("图纸号", "D001"),
        ("模具穴号", "1"),
        ("工程更改版本", "A"),
        ("版本日期", "2026-11-01"),
        ("街道地址", "示例路1号"),
        ("城市", "上海"),
        ("省", "上海"),
        ("国家", "中国"),
        ("顾客名称", "理想汽车"),
        ("设计开发", "甲"),
        ("采购工程", "乙"),
        ("PTC", "丙"),
    )
    for row, (label, value) in enumerate(fields, 4):
        sheet.cell(row, 1, label)
        sheet.cell(row, 2, value)
    delivery = workbook.create_sheet("生产件批准交付物文件清单")
    delivery.append(["项目", "附件"])
    delivery.append(["尺寸报告", "报告.xlsx"])
    if unknown:
        sheet_name, address = unknown
        workbook[sheet_name][address] = "#N/A"
        assert workbook[sheet_name][address].data_type == "e"
    workbook.save(path)
    workbook.close()


@pytest.mark.parametrize(("profile", "agent_type"), [("v2", V2PswAgent), ("v3", V3PswAgent)], ids=["v2", "v3"])
@pytest.mark.parametrize(
    ("unknown", "expected"),
    [
        (None, ["PASS", "PASS", "PASS", "PASS"]),
        (("零件提交保证书", "Z60"), ["PASS", "PASS", "PASS", "PASS"]),
        (("零件提交保证书", "B4"), ["PASS", "UNABLE", "PASS", "UNABLE"]),
        (("零件提交保证书", "A3"), ["PASS", "PASS", "UNABLE", "UNABLE"]),
        (("生产件批准交付物文件清单", "B2"), ["UNABLE", "PASS", "PASS", "PASS"]),
    ],
    ids=["all-native-known", "unrelated-unknown-footer", "unknown-weight", "unknown-supplier", "unknown-attachment"],
)
def test_native_psw_unknown_cells_do_not_become_filled_values_or_erase_known_rules(tmp_path, profile, agent_type, unknown, expected):
    path = tmp_path / "native-partial.xlsx"
    native_psw_fields(path, unknown=unknown)
    original = path.read_bytes()
    agent = agent_type(
        AuditInput(audit_id="native-partial", file_url=str(path)), RuleInput(rule_id="psw-known-native", rule_profile=profile, type="零件提交保证书", rules=[{"id": str(i)} for i in range(1, 11)])
    )
    try:
        with bind_runtime(None):
            results = [agent.check(index) for index in (0, 1, 3, 5)]
        assert [result.status for result in results] == expected
        assert all("#N/A" not in result.suggestions for result in results)
        assert path.read_bytes() == original
    finally:
        agent.close()

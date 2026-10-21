"""Embedded Excel images reach the existing V2/V3 seal and signature checks."""

from io import BytesIO

import pytest
from openpyxl import Workbook
from openpyxl.drawing.image import Image as WorkbookImage
from PIL import Image, ImageDraw
from ppap_core.contracts import AuditInput, RuleInput
from ppap_excel_audit_engine.checkers.agents.psw_excel_agent import PswExcelAgent as V2PswAgent
from ppap_excel_audit_engine.checkers.agents_v3_overlay.psw_excel_agent import PswExcelAgent as V3PswAgent
from ppap_excel_audit_engine.runtime import bind_runtime


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

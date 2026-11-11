"""Original PSW geometry and scope hints, with no model calls or Oracle changes."""

import hashlib
from pathlib import Path

import fitz
from audit.contracts import AuditInput
from audit.engine.checkers.pdf_field_region import pdf_field_region
from audit.engine.checkers.psw_scope import _Objects

SHA256 = "24133e74c1dac80339a694863930121b9525d4b0d887f7e020b9ed82f3014c0b"


def source():
    path = Path(__file__).resolve().parents[3] / "assets/理想/样本/模拟测试文件V2-01/16-零件提交保证书/16-零件提交保证书-上海亚大.pdf"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == SHA256
    return path


def test_shanghai_yada_instruction_labels_do_not_crop_away_the_warrant():
    with fitz.open(source()) as document:
        page = document[4]
        assert len(page.search_for("公司名称")) == 2
        assert pdf_field_region(page, {"psw_stamp_present", "psw_stamp_ending_text"}) == page.rect


def test_shanghai_yada_guidance_is_a_role_question_not_proof_from_blank_fields():
    objects = _Objects(AuditInput(audit_id="frozen-yada-scope", file_url=str(source())), SHA256, {})
    assert objects.warrants == [3, 5]
    assert objects.form_pages == {3, 5}
    assert objects.roles[3] is False
    # Native guidance requests an independent role observation; blankness is
    # neither a sample verdict nor a reason to omit the business form.
    assert objects.roles[5] is None

"""Business scope and quantifiers stay independent of missing required facts."""

from types import SimpleNamespace

import fitz
import pytest
from audit.contracts import AuditInput, AuditResultItem
from audit.engine.checkers.psw_pdf_evidence import PswPdfEvidenceMixin
from PIL import Image, ImageChops


def write_pdf(path, pages):
    with fitz.open() as document:
        for index, (role, signature, stamp) in enumerate(pages):
            page = document.new_page(width=600, height=850)
            page.insert_text((30, 20), "EXAMPLE" if role == "example" else f"DOCUMENT {index}", fontsize=10)
            page.insert_text((30, 50), "零件提交保证书", fontname="china-s", fontsize=12)
            if role == "unknown":
                page.insert_text((30, 85), "供应商名称填写为：", fontname="china-s", fontsize=10)
                page.insert_text((30, 100), "ROLE:UNKNOWN", fontsize=10)
            page.insert_text((400, 600), "公司名称（公司章）", fontname="china-s", fontsize=8)
            page.insert_text((40, 640), "经授权的供方代表签字", fontname="china-s", fontsize=8)
            page.insert_text((350, 640), "日期", fontname="china-s", fontsize=8)
            page.insert_text((40, 740), "认可情况", fontname="china-s", fontsize=8)
            if stamp:
                page.draw_rect(fitz.Rect(450, 625, 500, 665), color=(1, 0, 0), fill=(1, 0, 0))
            if signature:
                page.draw_rect(fitz.Rect(200, 632, 250, 640), color=(0, 1, 0), fill=(0, 1, 0))
        document.save(path)
    return path


@pytest.fixture(autouse=True)
def controlled_vision(monkeypatch):
    import io

    monkeypatch.setenv("OCR_MODEL_NAME", "controlled")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://controlled.invalid")
    calls = []

    async def observe(images, prompt, *, response_schema, **kwargs):
        calls.append(tuple(response_schema["schema"]["properties"]))
        stamp = signature = False
        for data in images:
            with Image.open(io.BytesIO(data)) as image:
                rgb = image.convert("RGB")
                stamp |= ImageChops.subtract(rgb.getchannel("R"), rgb.getchannel("G")).getbbox() is not None
                signature |= ImageChops.subtract(rgb.getchannel("G"), rgb.getchannel("R")).getbbox() is not None
        values = {
            "psw_stamp_present": stamp,
            "psw_stamp_ending_text": "公司" if stamp else "",
            "authorized_signature_present": signature,
            "authorization_table_present": "AUTH:BODY" in prompt,
            "authorization_company_stamp_present": stamp,
            "psw_reference_content": None if "ROLE:UNKNOWN" in prompt else "EXAMPLE" in prompt,
        }
        return {field: values.get(field) for field in response_schema["schema"]["properties"]}

    monkeypatch.setattr("documents.ocr.client.call_llm_async", observe)
    return calls


def checker(path):
    host = SimpleNamespace(
        audit_file=AuditInput(audit_id="frozen-scope", file_url=str(path)),
        get_rule_by_index=lambda index: True,
        rule=SimpleNamespace(rules=[{}] * 10),
        create_error_result=lambda message, index: AuditResultItem(classification="test", item="test", priority="P0", status="ERR", suggestions=message),
    )

    class Agent(PswPdfEvidenceMixin):
        def _host(self):
            return host

    return Agent()


def signature_result(path):
    return checker(path)._run_fact_rule(4, 5)


@pytest.mark.parametrize("sample", [(False, False), (True, True)])
@pytest.mark.parametrize("reverse", [False, True])
def test_adding_or_moving_reference_forms_does_not_change_business_result(tmp_path, sample, reverse):
    actual = ("business", True, True)
    reference = ("example", *sample)
    pages = [reference, actual] if reverse else [actual, reference]
    assert signature_result(write_pdf(tmp_path / "actual.pdf", [actual])).status == "PASS"
    assert signature_result(write_pdf(tmp_path / "mixed.pdf", pages)).status == "PASS"


def test_good_example_cannot_repair_a_missing_business_seal(tmp_path):
    path = write_pdf(tmp_path / "missing.pdf", [("business", True, False), ("example", True, True)])
    assert signature_result(path).status == "NG"


def test_multiple_forms_on_a_confirmed_reference_page_do_not_poison_business_scope(tmp_path):
    path = write_pdf(tmp_path / "reference-multiple-forms.pdf", [("business", True, True), ("example", False, False)])
    with fitz.open(path) as document:
        page = document[1]
        page.insert_text((40, 780), "经授权的供方代表签字", fontname="china-s", fontsize=8)
        page.insert_text((40, 810), "认可情况", fontname="china-s", fontsize=8)
        document.saveIncr()
    assert signature_result(path).status == "PASS"


@pytest.mark.parametrize("reverse", [False, True])
def test_signature_and_seal_from_different_business_forms_cannot_be_spliced(tmp_path, reverse):
    pages = [("business", True, False), ("business", False, True)]
    path = write_pdf(tmp_path / "two-objects.pdf", pages[::-1] if reverse else pages)
    assert signature_result(path).status == "NG"


def test_blank_business_form_stays_in_scope(tmp_path):
    path = write_pdf(tmp_path / "blank-business.pdf", [("business", True, True), ("business", False, False)])
    assert signature_result(path).status == "NG"


def test_only_filled_examples_cannot_create_a_business_pass(tmp_path):
    path = write_pdf(tmp_path / "only-example.pdf", [("example", True, True)])
    assert signature_result(path).status == "NG"


def test_unresolved_role_cannot_be_silently_excluded(tmp_path):
    path = write_pdf(tmp_path / "unknown-role.pdf", [("business", True, True), ("unknown", True, True)])
    assert signature_result(path).status == "UNABLE"


def test_removing_a_required_mark_never_removes_the_business_object(tmp_path):
    good = write_pdf(tmp_path / "before.pdf", [("business", True, True), ("example", True, True)])
    missing = write_pdf(tmp_path / "after.pdf", [("business", True, False), ("example", True, True)])
    assert signature_result(good).status == "PASS"
    assert signature_result(missing).status == "NG"


def test_known_business_counterexample_is_not_rescued_by_an_unresolved_role(tmp_path):
    path = write_pdf(tmp_path / "counterexample.pdf", [("business", True, False), ("unknown", True, True)])
    assert signature_result(path).status == "NG"


def test_authorization_body_and_company_seal_must_belong_to_one_object():
    from audit.engine.checkers.psw_scope import _exists_authorization

    assert _exists_authorization(
        [
            {"authorization_table_present": True, "authorization_company_stamp_present": False},
            {"authorization_table_present": False, "authorization_company_stamp_present": True},
        ],
        True,
    ) == (True, False)


@pytest.mark.parametrize("scope_known", [False, True])
def test_exists_accepts_a_known_witness_without_requiring_all_objects(scope_known):
    from audit.engine.checkers.psw_scope import _exists_authorization

    assert _exists_authorization(
        [
            {"authorization_table_present": True, "authorization_company_stamp_present": True},
            {"authorization_table_present": None, "authorization_company_stamp_present": None},
        ],
        scope_known,
    ) == (True, True)


def test_authorization_example_or_unknown_role_cannot_be_a_positive_witness():
    from audit.engine.checkers.psw_scope import REFERENCE, _exists_authorization

    valid = {"authorization_table_present": True, "authorization_company_stamp_present": True}
    assert _exists_authorization([{**valid, REFERENCE: True}], True) == (False, False)
    assert _exists_authorization([{**valid, REFERENCE: None}], True) == (None, None)


def test_unknown_seal_text_is_not_asked_again_as_a_derived_boolean(tmp_path, monkeypatch, controlled_vision):
    from documents.ocr import client

    original = client.call_llm_async

    async def unknown_text(*args, **kwargs):
        facts = await original(*args, **kwargs)
        if "psw_stamp_ending_text" in facts:
            facts["psw_stamp_ending_text"] = None
        return facts

    monkeypatch.setattr(client, "call_llm_async", unknown_text)
    agent = checker(write_pdf(tmp_path / "unknown-ending.pdf", [("business", True, True)] * 2))
    assert agent._run_fact_rule(2, 3).status == "UNABLE"
    assert all("psw_stamp_ends_with_company" not in fields for fields in controlled_vision)
    assert sum("psw_stamp_ending_text" in fields for fields in controlled_vision) == 4
    count = len(controlled_vision)
    assert agent._run_fact_rule(2, 3).status == "UNABLE"
    assert len(controlled_vision) == count


def test_disagreed_role_stays_unknown_without_a_third_observation(tmp_path, monkeypatch, controlled_vision):
    from documents.ocr import client

    original = client.call_llm_async

    async def disagreed_role(*args, **kwargs):
        facts = await original(*args, **kwargs)
        if "psw_reference_content" in facts:
            facts["psw_reference_content"] = client.current_ocr_observation() == 1
        return facts

    monkeypatch.setattr(client, "call_llm_async", disagreed_role)
    agent = checker(write_pdf(tmp_path / "role-disagreement.pdf", [("business", True, True), ("unknown", True, True)]))
    assert agent._run_fact_rule(4, 5).status == "UNABLE"
    assert sum("psw_reference_content" in fields for fields in controlled_vision) == 2
    count = len(controlled_vision)
    assert agent._run_fact_rule(4, 5).status == "UNABLE"
    assert len(controlled_vision) == count


@pytest.mark.parametrize("repeated_header", [False, True])
def test_reference_exclusion_does_not_make_section_fragments_independent_objects(tmp_path, repeated_header):
    path = write_pdf(tmp_path / "section-fragments.pdf", [("business", True, False), ("business", False, True), ("example", True, True)])
    with fitz.open(path) as document:
        for index, page in enumerate(list(document)[:2]):
            label = ("认可情况" if index == 0 else "经授权的供方代表签字") if repeated_header else "零件提交保证书"
            for bounds in page.search_for(label):
                page.add_redact_annot(bounds)
            page.apply_redactions()
        document.saveIncr()
    assert signature_result(path).status == "UNABLE"


def force_department_stamp(monkeypatch):
    from documents.ocr import client

    original = client.call_llm_async

    async def department_stamp(*args, **kwargs):
        facts = await original(*args, **kwargs)
        if "psw_stamp_ending_text" in facts:
            facts["psw_stamp_ending_text"] = "专用章"
        return facts

    monkeypatch.setattr(client, "call_llm_async", department_stamp)


@pytest.mark.parametrize("witness", [False, True])
def test_authorization_exists_is_composed_per_object_through_psw(tmp_path, monkeypatch, witness):
    path = write_pdf(tmp_path / "authorization.pdf", [("business", True, True)])
    with fitz.open(path) as document:
        objects = [(True, False), (False, True)]
        if witness:
            objects.append((True, True))
        for body, seal in objects:
            page = document.new_page(width=600, height=850)
            page.insert_text((30, 40), "部门章授权声明", fontname="china-s", fontsize=12)
            page.insert_text((30, 80), "AUTH:BODY" if body else "AUTH:LIST", fontsize=10)
            if seal:
                page.draw_rect(fitz.Rect(450, 625, 500, 665), color=(1, 0, 0), fill=(1, 0, 0))
        document.saveIncr()
    force_department_stamp(monkeypatch)
    assert checker(path)._run_fact_rule(2, 3).status == ("PASS" if witness else "NG")


def test_separate_authorizations_on_one_page_are_not_a_composite_witness(tmp_path, monkeypatch):
    path = write_pdf(tmp_path / "two-authorizations-one-page.pdf", [("business", True, True)])
    with fitz.open(path) as document:
        page = document.new_page(width=600, height=850)
        page.insert_text((30, 40), "部门章授权声明", fontname="china-s", fontsize=12)
        page.insert_text((30, 80), "AUTH:BODY", fontsize=10)
        page.insert_text((30, 400), "部门章授权声明", fontname="china-s", fontsize=12)
        page.insert_text((30, 440), "AUTH:LIST", fontsize=10)
        page.draw_rect(fitz.Rect(450, 625, 500, 665), color=(1, 0, 0), fill=(1, 0, 0))
        document.saveIncr()
    force_department_stamp(monkeypatch)
    assert checker(path)._run_fact_rule(2, 3).status == "UNABLE"


def test_seal_rule_cannot_take_a_company_seal_from_an_example(tmp_path):
    path = write_pdf(tmp_path / "example-company-seal.pdf", [("business", True, False), ("example", True, True)])
    assert checker(path)._run_fact_rule(2, 3).status == "NG"


def test_one_business_fact_disagreement_is_not_a_quantifier_counterexample(tmp_path, monkeypatch):
    from documents.ocr import client

    original = client.call_llm_async

    async def disagreed_seal(*args, **kwargs):
        facts = await original(*args, **kwargs)
        if "psw_stamp_present" in facts:
            facts["psw_stamp_present"] = client.current_ocr_observation() == 1
        return facts

    monkeypatch.setattr(client, "call_llm_async", disagreed_seal)
    path = write_pdf(tmp_path / "one-fact.pdf", [("business", True, True), ("example", True, True)])
    assert signature_result(path).status == "UNABLE"


def test_scope_diagnostics_do_not_log_page_text_or_file_name(tmp_path, caplog):
    path = write_pdf(tmp_path / "sensitive-customer.pdf", [("business", True, True), ("unknown", True, True)])
    assert signature_result(path).status == "UNABLE"
    assert '"unresolved_pages":[2]' in caplog.text
    assert "psw_reference_content" in caplog.text
    assert path.name not in caplog.text
    assert "ROLE:UNKNOWN" not in caplog.text
    assert "供应商名称填写为" not in caplog.text

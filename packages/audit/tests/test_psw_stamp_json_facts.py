"""PSW uses source-located boolean observations with reasons, not text slicing."""

from unittest.mock import Mock

import fitz
import pytest
from audit.contracts import AuditInput
from audit.engine.checkers.ocr_visual_facts import OcrImage, observe_ocr_images
from audit.engine.checkers.psw_visual_evidence import PSW_RULE_FIELDS, evaluate_psw_observation
from documents.ocr.observation import current_ocr_observation

COMPANY = "psw_stamp_ends_with_company"


@pytest.fixture(autouse=True)
def no_live_provider(monkeypatch):
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled-ocr")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled-noncredential")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://ocr.invalid/v1")
    monkeypatch.setattr("documents.ai.AsyncOpenAI", lambda **kwargs: pytest.fail("This regression must not call a live provider"))


def read_pair(monkeypatch, first, second, *, located=False, source=True, mutate=None):
    requests = []
    readings = (first, second)

    async def read(_images, prompt, *, response_schema, **_options):
        requests.append((prompt, response_schema))
        present, value, reason = readings[current_ocr_observation() - 1]
        if located:
            raw = {"psw_stamp_present": present, COMPANY: {"value": value, "reason": reason}}
        else:
            raw = {}
            for field, observed in (("psw_stamp_present", present), (COMPANY, value)):
                detail = "reason" if field == COMPANY else "excerpt"
                raw[field] = [
                    {
                        "scope": "pdf:2",
                        "value": observed,
                        "sources": [{"image_index": 0, "bbox": [0, 0, 1, 1]}] if source and observed is not None else [],
                        "covered_image_indices": [0] if observed is not None else [],
                        "coverage": "complete" if observed is not None else "unreadable",
                        detail: reason if field == COMPANY else "实际盖章观察",
                    }
                ]
        if mutate:
            mutate(raw, located)
        return raw

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.read_visual_facts", read)
    location = {"scope": "pdf:2", "page": 2, "bbox": [10, 20, 110, 220], "context": True}
    if located:
        location["located_region"] = True
    image = OcrImage(b"controlled-image", location, 100, 200)
    witness = Mock()
    fields = {name: PSW_RULE_FIELDS[3][name] for name in ("psw_stamp_present", COMPANY)}
    facts = observe_ocr_images([image], fields, witness)
    return facts, witness, requests


@pytest.mark.parametrize("located", [False, True])
@pytest.mark.parametrize(("value", "expected"), [(True, "PASS"), (False, "NG"), (None, "UNABLE")])
def test_company_target_uses_strict_boolean_reason_schema_and_keeps_both_reasons(monkeypatch, located, value, expected):
    reasons = (
        "完整原图的最终文字可辨，按验证目标作出观察" if value is not None else "原图模糊，不能确认最终文字",
        "复核同一原图的最终文字与用途位置" if value is not None else "用途文字的阅读顺序无法确认",
    )
    facts, witness, requests = read_pair(monkeypatch, (True, value, reasons[0]), (True, value, reasons[1]), located=located)
    facts.update(authorization_table_present=True, authorization_company_stamp_present=False)
    assert evaluate_psw_observation(3, facts).status == expected
    assert "psw_stamp_ending_text" not in facts and "psw_stamp_ending_text" not in PSW_RULE_FIELDS[3]
    observations = [call for call in witness.call_args_list if call.args[0]["fact"] == COMPANY]
    assert [call.args[1] for call in observations] == list(reasons)
    assert [call.args[0]["observation"] for call in observations] == [1, 2]
    if value is None:
        assert all(call.args[0]["coverage"] == "unreadable" and call.args[0]["observed_value"] is None and call.args[0]["bbox"] == [10, 20, 110, 220] for call in observations)
    for prompt, schema in requests:
        assert "内部" in prompt and "reason" in prompt
        item = schema["schema"]["properties"][COMPANY]
        if not located:
            item = item["items"]
        assert item["additionalProperties"] is False
        assert {"value", "reason"} <= set(item["required"])
        assert item["properties"]["value"]["type"] == ["boolean", "null"]
        assert item["properties"]["reason"] == {"type": "string", "minLength": 1, "maxLength": 160}


def test_company_boolean_conflict_stays_unknown_and_retains_disagreement_reasons(monkeypatch):
    facts, witness, _requests = read_pair(monkeypatch, (True, True, "看到最终文字为公司"), (True, False, "底部还有用途文字"))
    facts.update(authorization_table_present=True, authorization_company_stamp_present=False)
    assert evaluate_psw_observation(3, facts).status == "UNABLE"
    observations = [call for call in witness.call_args_list if call.args[0]["fact"] == COMPANY]
    assert [call.args[1] for call in observations] == ["看到最终文字为公司", "底部还有用途文字"]
    assert [call.args[0]["reported_value"] for call in observations] == [True, False]
    assert all(call.args[0]["observed_value"] is None and call.args[0]["coverage"] == "conflicting" for call in observations)


@pytest.mark.parametrize("present", [None, False])
def test_presence_conflict_or_unknown_cannot_establish_the_company_target(monkeypatch, present):
    facts, _witness, _requests = read_pair(monkeypatch, (True, True, "最终文字可见"), (present, True, "最终文字可见"))
    facts.update(authorization_table_present=True, authorization_company_stamp_present=False)
    assert evaluate_psw_observation(3, facts).status == "UNABLE"


@pytest.mark.parametrize("reason", [None, "", " ", 3, "字" * 161])
def test_company_reason_is_required_nonempty_bounded_text(monkeypatch, reason):
    with pytest.raises(ValueError):
        read_pair(monkeypatch, (True, False, reason), (True, False, reason), located=True)


def test_company_known_fact_requires_original_sources(monkeypatch):
    with pytest.raises(ValueError):
        read_pair(monkeypatch, (True, False, "用途文字不以公司结束"), (True, False, "末尾非公司"), source=False)


@pytest.mark.parametrize("mode", ["missing-reason", "extra-confidence", "old-primitive"])
def test_located_company_response_has_one_canonical_value_reason_shape(monkeypatch, mode):
    def mutate(raw, _located):
        if mode == "missing-reason":
            del raw[COMPANY]["reason"]
        elif mode == "extra-confidence":
            raw[COMPANY]["confidence"] = 1
        else:
            raw[COMPANY] = False

    with pytest.raises(ValueError):
        read_pair(monkeypatch, (True, False, "末尾可辨"), (True, False, "末尾可辨"), located=True, mutate=mutate)


def test_single_pdf_uses_the_shared_object_reader_not_legacy_text_bridge(tmp_path, monkeypatch):
    from audit.engine.checkers import psw_scope

    path = tmp_path / "warrant.pdf"
    with fitz.open() as document:
        page = document.new_page()
        for y, text in ((40, "零件提交保证书"), (100, "经授权的供方代表签字"), (200, "认可情况")):
            page.insert_text((30, y), text, fontname="china-s")
        document.save(path)
    calls = []

    def observe(images, fields, witness, **_options):
        calls.append(set(fields))
        assert "psw_stamp_ending_text" not in fields
        return {"pdf:1": {field: True for field in fields}}

    monkeypatch.setattr(psw_scope, "observe_ocr_scopes", observe)
    audit = AuditInput(audit_id="frozen-version", file_url=str(path))
    _facts, result = psw_scope.evaluate_psw_pdf_rule(audit, 3, Mock(), {}, lambda: pytest.fail("The retired single-PDF text bridge must not run"))
    assert result.status == "PASS"
    assert calls == [{"psw_stamp_present", COMPANY}]


def test_incomplete_company_coverage_keeps_reason_without_claiming_a_known_false(monkeypatch):
    def mutate(raw, _located):
        raw[COMPANY][0]["covered_image_indices"] = []

    facts, witness, _requests = read_pair(monkeypatch, (True, False, "局部用途文字可见但范围不完整"), (True, False, "无法确认全部目标范围"), mutate=mutate)
    assert facts[COMPANY] is None
    observations = [call for call in witness.call_args_list if call.args[0]["fact"] == COMPANY]
    assert len(observations) == 2
    assert [call.args[1] for call in observations] == ["局部用途文字可见但范围不完整", "无法确认全部目标范围"]
    assert all(call.args[0]["observed_value"] is None and call.args[0]["coverage"] == "partial" and call.args[0]["reported_value"] is False for call in observations)


@pytest.mark.parametrize(
    ("scenario", "rule", "expected"),
    [
        ("unread-warrant", 3, "UNABLE"),
        ("blank", 3, "PASS"),
        ("known-counterexample", 5, "NG"),
        ("unread-authorization", 3, "UNABLE"),
        ("native-checklist", 3, "PASS"),
        ("unclassified-native", 3, "UNABLE"),
        ("white-vector-blank", 3, "PASS"),
        ("unread-vector", 3, "UNABLE"),
        ("neutral-vector-structure", 3, "PASS"),
        ("native-non-submission", 3, "PASS"),
        ("non-submission-title-only", 3, "UNABLE"),
        ("english-native-vector", 3, "UNABLE"),
        ("english-authorization-vector", 3, "UNABLE"),
    ],
)
def test_unclassified_visual_pages_keep_all_and_exists_domains_incomplete(tmp_path, monkeypatch, scenario, rule, expected):
    from audit.engine.checkers import psw_scope

    with fitz.open() as raster:
        page = raster.new_page()
        page.insert_text(
            (30, 40),
            "企业标志" if scenario in {"native-checklist", "native-non-submission", "non-submission-title-only"} else "部门章授权声明" if scenario == "unread-authorization" else "零件提交保证书",
            fontname="china-s",
        )
        page.insert_text((30, 100), "图形原件内容", fontname="china-s")
        image = page.get_pixmap().tobytes("png")
    path = tmp_path / "domain.pdf"
    with fitz.open() as document:
        page = document.new_page()
        for y, text in ((40, "零件提交保证书"), (100, "经授权的供方代表签字"), (200, "认可情况")):
            page.insert_text((30, y), text, fontname="china-s")
        if scenario in {"unread-authorization", "english-authorization-vector"}:
            page = document.new_page()
            page.insert_text((30, 40), "部门章授权声明", fontname="china-s")
        page = document.new_page()
        if scenario == "native-checklist":
            for y, text in ((30, "生产件批准交付物文件清单"), (60, "序号 项目 确认方法 交付形式 附件")):
                page.insert_text((30, y), text, fontname="china-s")
        elif scenario in {"native-non-submission", "non-submission-title-only"}:
            page.insert_text((30, 30), "PPAP文件无法提交声明", fontname="china-s")
            if scenario == "native-non-submission":
                page.insert_text((30, 60), "序号 文件类型 无法提交，可支持现场查阅审核", fontname="china-s")
        elif scenario == "neutral-vector-structure":
            page.insert_text((30, 30), "Comments")
        elif scenario == "unclassified-native":
            page.insert_text((30, 30), "其它原生文字不能排除图形业务表", fontname="china-s")
        elif scenario in {"english-native-vector", "english-authorization-vector"}:
            page.insert_text((30, 30), "Department Seal Authorization" if scenario == "english-authorization-vector" else "Part Submission Warrant")
            page.insert_text((30, 60), "Authorized Supplier Signature")
        if scenario in {"english-native-vector", "english-authorization-vector"}:
            page.draw_circle((120, 180), 40, color=(1, 0, 0))
        elif scenario == "unread-vector":
            page.draw_circle((120, 180), 40, color=(0, 0, 0))
        elif scenario in {"white-vector-blank", "neutral-vector-structure"}:
            page.draw_rect(fitz.Rect(30, 90, 250, 300), color=None, fill=(1, 1, 1) if scenario == "white-vector-blank" else (0, 0, 0))
        elif scenario != "blank":
            page.insert_image(fitz.Rect(30, 90, 250, 300), stream=image)
        document.save(path)
    before = path.read_bytes()
    calls = []

    def observe(images, fields, witness, **options):
        calls.append({int(image.locator["page"]) for image in images})
        result = {}
        for field, scopes in options["field_scopes"].items():
            for scope in scopes:
                if field == COMPANY:
                    value = scenario not in {"unread-authorization", "english-authorization-vector"}
                elif field.startswith("authorization_"):
                    value = scenario in {"unread-authorization", "english-authorization-vector"} and field == "authorization_table_present"
                elif field == "authorized_signature_present":
                    value = scenario != "known-counterexample"
                else:
                    value = True
                result.setdefault(scope, {})[field] = value
        return result

    monkeypatch.setattr(psw_scope, "observe_ocr_scopes", observe)
    audit = AuditInput(audit_id="frozen-domain", file_url=str(path))
    witness = Mock()
    _facts, result = psw_scope.evaluate_psw_pdf_rule(audit, rule, witness, {}, lambda: pytest.fail("Retired bridge must not run"))
    assert result.status == expected
    assert path.read_bytes() == before
    assert calls and all(max(pages) <= (2 if scenario in {"unread-authorization", "english-authorization-vector"} else 1) for pages in calls)
    if scenario == "native-checklist":
        identity = next(call for call in witness.call_args_list if call.args[0].get("coverage") == "native_document_heading")
        assert identity.args[0]["page"] == 2
        import hashlib

        assert identity.args[0]["source_sha256"] == hashlib.sha256(before).hexdigest()
        with fitz.open(path) as document:
            assert document[1].get_textbox(fitz.Rect(identity.args[0]["bbox"])).strip() == "生产件批准交付物文件清单"

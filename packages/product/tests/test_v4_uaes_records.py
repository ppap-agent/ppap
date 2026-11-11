"""Independent source facts for UAES's simple records and photo boundary."""

import pytest
from audit.checker_lib.uaes.records import UaesEngineeringHistoryAgent, UaesLabQualificationAgent, UaesPackagingAgent, UaesPhotoAgent, UaesTraceabilityAgent, UaesTrialAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook
from PIL import Image


def checked(tmp_path, agent_cls, entry, rows, *, title="原件"):
    book = Workbook()
    book.active.title = title
    for row in rows:
        book.active.append(row)
    path = tmp_path / "source.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", entry)
    agent = agent_cls(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="source-rules", **{key: value for key, value in body.items() if key != "kind"}))
    return agent.check(0)


@pytest.mark.parametrize("missing", [None, "包装材料清单"])
def test_packaging_uses_original_content_not_the_filename(tmp_path, missing):
    sections = ["包装方案", "零件基础信息", "供应商基础信息", "包装设计的基本要求", "针对该零件包装的特殊要求", "包装方案信息", "包装材料清单"]
    result = checked(tmp_path, UaesPackagingAgent, "U09", [[section] for section in sections if section != missing])
    assert result.status == ("PASS" if missing is None else "NG")
    assert result.evidence and result.evidence.sources


def test_engineering_history_accepts_declared_business_aliases(tmp_path):
    result = checked(tmp_path, UaesEngineeringHistoryAgent, "U11", [["工程更改一览表"], ["项目代号", "物料编号", "更改内容", "更改原因", "更改日期"]], title="工程更改")
    assert result.status == "PASS"


def test_traceability_area_labels_do_not_replace_their_content(tmp_path):
    labels = ["基本信息", "分供方追溯", "生产过程追溯", "交付追溯", "追溯状态总结"]
    result = checked(tmp_path, UaesTraceabilityAgent, "U18", [["供应商零件追溯"]] + [[label, None if label == "分供方追溯" else "实际追溯内容"] for label in labels], title="供应商零件追溯")
    assert result.status == "UNABLE"


@pytest.mark.parametrize(("date_text", "expected"), [("2099-12-31", "UNABLE"), ("2000-01-01", "NG"), ("not readable", "UNABLE")])
def test_laboratory_expiry_uses_the_actual_deadline(tmp_path, date_text, expected):
    result = checked(tmp_path, UaesLabQualificationAgent, "U03", [["中国合格评定国家认可委员会"], ["CNAS"], ["截止日期", date_text]])
    assert result.status == expected


@pytest.mark.parametrize(("trial", "inspection", "expected"), [("☑通过", "☑合格", "PASS"), ("☑未通过", "☑合格", "NG"), ("通过", "合格", "UNABLE")])
def test_trial_requires_actual_unambiguous_selections(tmp_path, trial, inspection, expected):
    result = checked(tmp_path, UaesTrialAgent, "U15", [["流转记录", "流程结束 流程完成"], ["试装结论", trial], ["检验结论", inspection]])
    assert result.status == expected


def test_photo_success_does_not_claim_correct_part_content(tmp_path):
    path = tmp_path / "photo.png"
    Image.new("RGB", (16, 12), "white").save(path)
    body = preset_rule_body("UAES", "U13")
    agent = UaesPhotoAgent(AuditInput(audit_id="photo-version", file_url=str(path)), RuleInput(rule_id="photo-rules", **{key: value for key, value in body.items() if key != "kind"}))
    result = agent.check(0)
    assert result.status == "PASS"
    assert "需人工确认样品照片是否符合要求" in result.suggestions


@pytest.mark.parametrize(
    ("text", "detected", "expected"),
    [("受控", True, "PASS"), ("受控文件", True, "PASS"), ("存档资料", True, "UNABLE"), ("不受控", True, "UNABLE"), ("非受控", True, "UNABLE"), ("受控", False, "UNABLE")],
)
def test_controlled_stamp_uses_located_paddle_seal_facts_not_preprinted_labels(tmp_path, monkeypatch, text, detected, expected):
    from types import SimpleNamespace

    import fitz
    from audit.checker_lib.uaes.records import UaesControlledDocumentAgent
    from audit.engine.runtime import bind_runtime
    from documents.ocr.paddle import build_evidence

    path = tmp_path / "controlled.pdf"
    with fitz.open() as document:
        document.new_page(width=400, height=200).insert_text((20, 20), "controlled stamp label")
        document.save(path)
    original = path.read_bytes()
    reads, calls = [], []

    def read(file_path, page_number):
        reads.append((file_path, page_number))
        raw = {
            "dataInfo": {"pages": [{"width": 400, "height": 200}]},
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "layout_det_res": {"boxes": [{"label": "seal", "coordinate": [10, 10, 60, 60]}] if detected else []},
                        "seal_res_list": [{"rec_texts": [text], "bbox": [10, 10, 60, 60]}] if detected else [],
                        "parsing_res_list": [],
                        "table_res_list": [],
                    }
                }
            ],
        }
        return build_evidence(raw, page_count=1, pdf_path=file_path, source_pages=[page_number])

    monkeypatch.setattr("documents.ai.run_json", lambda *args, **kwargs: calls.append(kwargs))
    body = preset_rule_body("UAES", "U10")
    agent = UaesControlledDocumentAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="rules", **{key: value for key, value in body.items() if key != "kind"}))
    with bind_runtime(SimpleNamespace(read_paddle_page=read)):
        result = agent.check(0)
        second = agent.check(0)
    assert result.status == second.status == expected
    assert reads == [(str(path), 1)] and calls == []
    assert result.evidence.used == ("visual" if detected else "none")
    assert all(source.kind == "visual" for source in result.evidence.sources)
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("variant", "expected"), [("same", "PASS"), ("wrong_part", "UNABLE"), ("wrong_customer", "UNABLE"), ("wrong_revision", "UNABLE"), ("duplicate", "UNABLE"), ("unreadable", "ERR")]
)
def test_materials_only_pairs_current_unique_matching_originals(tmp_path, variant, expected):
    from audit.checker_lib.uaes.materials import UaesMaterialsAgent
    from audit.contracts import GroupContextInput

    def original(filename, *, part="P12345", customer="UAES", revision="A"):
        book = Workbook()
        book.active.title = "图纸" if "图纸" in filename else "材料清单"
        for row in [["零件号", part], ["零件名称", "支架"], ["客户名称", customer], ["版本", revision], ["项目", "M1"]]:
            book.active.append(row)
        path = tmp_path / filename
        book.save(path)
        book.close()
        return AuditInput(audit_id=filename, file_url=str(path), file_metadata={"original_filename": filename})

    target = original("清单.xlsx")
    reference = original(
        "图纸.xlsx", part="WRONG" if variant == "wrong_part" else "P12345", customer="理想" if variant == "wrong_customer" else "UAES", revision="B" if variant == "wrong_revision" else "A"
    )
    references = {target.audit_id: target, reference.audit_id: reference}
    if variant == "duplicate":
        extra = original("图纸副本.xlsx")
        references[extra.audit_id] = extra
    if variant == "unreadable":
        from pathlib import Path

        Path(reference.file_url).write_bytes(b"corrupt supplied source")
    body = preset_rule_body("UAES", "U04")
    agent = UaesMaterialsAgent(target, RuleInput(rule_id="rules", **{key: value for key, value in body.items() if key != "kind"}), GroupContextInput(by_audit_id=references))
    result = agent.check(2)
    assert result.status == expected, result.suggestions
    if expected == "PASS":
        import json

        assert {json.loads(source.locator)["file_version_id"] for source in result.evidence.sources} == {"清单.xlsx", "图纸.xlsx"}


def test_l2_precheck_reports_individual_source_facts_with_no_false_certification(tmp_path):
    book = Workbook()
    sections = ["包装方案", "零件基础信息", "供应商基础信息", "包装设计的基本要求", "针对该零件包装的特殊要求", "包装方案信息", "包装材料清单"]
    for phrase in sections:
        book.active.append([phrase])
    path = tmp_path / "packaging.xlsx"
    book.save(path)
    book.close()
    body = preset_rule_body("UAES", "U09")
    agent = UaesPackagingAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="rules", **{key: value for key, value in body.items() if key != "kind"}))
    facts = agent.precheck()
    assert {fact["id"] for fact in facts} == {f"U09:S{i}" for i in range(1, 8)}
    assert all(fact["status"] == "satisfied" and fact["evidence"]["sources"] for fact in facts)
    assert all(fact["certified"] is False for fact in facts)

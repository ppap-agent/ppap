"""Actual frozen E07 identity cannot corrupt an independent formal pair."""

import io

import pytest
from audit.audits import RoundInput, RoundWork
from audit.presets import preset_rule, preset_rule_body
from corpus_contracts.core_round import customer_suite, run_suite_round
from openpyxl import Workbook
from PIL import Image


def workbook(target):
    book = Workbook()
    book.active.title = "PFMEA" if target else "特殊特性清单"
    for row in [["客户", "UAES"], ["零件号", "P1"], ["零件名称", "支架"], ["版本", "A"], ["功能/要求", "特殊特性"] if target else ["特性名称", "特性类别"], ["孔径", "CC"]]:
        book.active.append(row)
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


@pytest.mark.parametrize("photo_available", [True, False])
def test_actual_round_keeps_sample_photo_type_and_independent_pair_result(photo_available):
    inputs, blobs = [], {}
    for role, version, entry, filename, data in [
        ("target", "pfmea", "U07", "PFMEA.xlsx", workbook(True)),
        ("reference", "scl", "U12", "supplier-file.xlsx", workbook(False)),
        ("target", "photo", "U13", "sample.png", None),
    ]:
        body = preset_rule_body("UAES", entry)
        inputs.append(RoundInput(role, version, version, filename, "ready", rule_version_id="rule-" + entry, rule_body=body))
        if data:
            blobs[version] = data
    if photo_available:
        stream = io.BytesIO()
        Image.new("RGB", (16, 12), "white").save(stream, format="PNG")
        blobs["photo"] = stream.getvalue()
    result = run_suite_round(RoundWork("round", "tenant", inputs), blobs, suites={"pfmea": customer_suite("UAES", "U07"), "scl": customer_suite("UAES", "U12"), "photo": customer_suite("UAES", "U13")})
    source = preset_rule("UAES", "U07").clauses[2].id
    finding = next(row for row in result["findings"] if row["item_key"] == "pfmea:rule:" + source)
    assert finding["status"] == "PASS", finding
    if not photo_available:
        assert any(row["status"] == "ERR" and row["locator"]["file_version_id"] == "photo" for row in result["findings"])

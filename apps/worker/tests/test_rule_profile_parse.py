"""Parsed rule files keep the profile contract without the old task API."""

from __future__ import annotations

import json
from io import BytesIO

import pytest
from audit.rule_profile import RULE_PROFILE_V2, RULE_PROFILE_V3
from product.rules import RuleParseError
from worker.intake import parse_rule_file


def test_structured_word_requirements_keep_exact_original_text_and_use_v3():
    from docx import Document
    from worker.intake.rule_sources import rule_sources

    doc = Document()
    table = doc.add_table(rows=3, cols=3)
    for cell, text in zip(table.rows[0].cells, ("审核条款", "规则说明", "样例/具体位置"), strict=True):
        cell.text = text
    requirements = [
        ("incoming", "识别“工序名称”列是否存在，如果存在则继续识别；当“工序名称”列下包含“进料检验”或“IQC”或相近词义的内容则通过，否则不通过"),
        ("custom-9", "保留未支持的要求。\n否则不通过；不得由模型改写。"),
    ]
    for row, (identity, text) in zip(table.rows[1:], requirements, strict=True):
        row.cells[0].text, row.cells[1].text = identity, text
    out = BytesIO()
    doc.save(out)

    class NoModel:
        def structured_completion(self, **kwargs):
            raise AssertionError("A complete rule table must not be rewritten by a model")

    body = parse_rule_file("renamed-requirements.docx", out.getvalue(), document_type="过程流程图", ai=NoModel())
    assert body["rule_profile"] == RULE_PROFILE_V3
    assert [(row["id"], row["rule_to_check"]) for row in body["rules"]] == requirements
    originals = {unit["excerpt"] for unit in rule_sources("renamed-requirements.docx", out.getvalue())}
    assert all(row["rule_to_check"] in originals for row in body["rules"])


@pytest.mark.parametrize("bad_row, code", [(("2", ""), "rule_table_incomplete"), (("1", "新增要求"), "duplicate_rule_id")])
def test_structured_word_requirements_reject_incomplete_or_duplicate_rows(bad_row, code):
    from docx import Document

    doc = Document()
    table = doc.add_table(rows=3, cols=2)
    for row, values in zip(table.rows, [("编号", "要求正文"), ("1", "必须填写零件号"), bad_row], strict=True):
        for cell, text in zip(row.cells, values, strict=True):
            cell.text = text
    out = BytesIO()
    doc.save(out)
    with pytest.raises(ValueError, match=f"^{code}$"):
        parse_rule_file("renamed.docx", out.getvalue(), document_type="控制计划")


@pytest.mark.parametrize("extra", ["paragraph", "scope_column", "duplicate_body_header"])
def test_structured_word_extraction_does_not_drop_scope_or_choose_ambiguous_headers(extra):
    from docx import Document

    doc = Document()
    if extra == "paragraph":
        doc.add_paragraph("只适用于客户指定的版本。")
    headers = ["编号", "要求正文"] + (["适用范围"] if extra == "scope_column" else ["要求正文"] if extra == "duplicate_body_header" else [])
    table = doc.add_table(rows=2, cols=len(headers))
    for cell, text in zip(table.rows[0].cells, headers, strict=True):
        cell.text = text
    table.cell(1, 0).text, table.cell(1, 1).text = "1", "必须填写零件号"
    if len(headers) == 3:
        table.cell(1, 2).text = "需保留的额外信息"
    out = BytesIO()
    doc.save(out)

    class ReadAllInstructions:
        called = False

        def structured_completion(self, **kwargs):
            self.called = True
            assert ("只适用于客户指定的版本。" if extra == "paragraph" else "需保留的额外信息") in kwargs["user_prompt"]
            return json.dumps({"type": "控制计划", "rules": [_item()]})

    model = ReadAllInstructions()
    body = parse_rule_file("renamed.docx", out.getvalue(), document_type="控制计划", ai=model)
    assert model.called
    assert body["rule_profile"] == RULE_PROFILE_V3


def _item() -> dict:
    return {
        "id": "1",
        "category": "基本信息",
        "priority": "P0",
        "rule_to_check": "必须填写零件号",
        "type_of_rule": "table",
    }


def test_json_parse_without_profile_becomes_v2() -> None:
    payload = {"kind": "engine", "type": "控制计划", "rules": [_item()]}
    body = parse_rule_file("rules.json", json.dumps(payload).encode())
    assert body["rule_profile"] == RULE_PROFILE_V2
    assert body["type"] == "控制计划"


def test_json_parse_keeps_explicit_v3() -> None:
    payload = {"kind": "engine", "type": "控制计划", "rule_profile": "v3", "rules": [_item()]}
    body = parse_rule_file("rules.json", json.dumps(payload).encode())
    assert body["rule_profile"] == RULE_PROFILE_V3


def test_json_parse_maps_default_profile_to_v2() -> None:
    payload = {"kind": "engine", "type": "控制计划", "rule_profile": "default", "rules": [_item()]}
    body = parse_rule_file("rules.json", json.dumps(payload).encode())
    assert body["rule_profile"] == RULE_PROFILE_V2


def test_json_parse_rejects_a_different_requested_document_type() -> None:
    payload = {"kind": "engine", "type": "控制计划", "rules": [_item()]}
    content = json.dumps(payload).encode()
    with pytest.raises(RuleParseError, match="^rule_document_type_mismatch$"):
        parse_rule_file("rules.json", content, document_type="全功能检验报告")
    assert parse_rule_file("rules.json", content, document_type="控制计划")["type"] == "控制计划"

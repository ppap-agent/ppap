from __future__ import annotations

from pathlib import Path

import pytest
from audit.checker_lib.li.psw_excel_agent import PswExcelAgent
from audit.checker_lib.li.psw_pdf_agent import PswPDFAgent
from audit.contracts import AuditInput, RuleInput
from audit.engine.rule_engine import Table
from audit.kinds import PpapDocumentType
from corpus_contracts.psw_cross_format_regression import (
    DifferenceKind,
    PswAuditSnapshot,
    compare_psw_snapshots,
)
from documents.contracts import FileRef
from documents.conversion import convert_pdf_to_excel_auto
from openpyxl import load_workbook

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PAIR_ROOT = _REPO_ROOT / "assets" / "li" / "samples" / "模拟测试文件V2-01" / "16-零件提交保证书" / "16-零件提交保证书-上海亚大"


def _run(agent_type, audit: AuditInput, rules: RuleInput) -> list:
    agent = agent_type(audit, rules)
    try:
        return [agent.check(index) for index in range(len(rules.rules))]
    finally:
        agent.close()


def _controlled_visual_facts(response_schema, image_count, prompt=""):
    values = {"authorized_signature_present": True, "psw_stamp_present": True, "submission_any_checked": True}
    result = {}
    for field, schema in response_schema["schema"]["properties"].items():
        company = field == "psw_stamp_ends_with_company"
        value = values.get(field)
        if "items" not in schema:
            result[field] = {"value": value, "reason": "受控测试未提供公司结尾事实"} if company else value
            continue
        observations = []
        for scope in schema["items"]["properties"]["scope"]["enum"]:
            indices = [int(line.split(":", 1)[0].split()[1]) for line in prompt.splitlines() if line.startswith("图 ") and f"'scope': '{scope}'" in line] or list(range(image_count))
            observations.append(
                {
                    "scope": scope,
                    "value": value,
                    "sources": [] if company and value is None else [{"image_index": indices[0], "bbox": [0, 0, 1, 1]}],
                    "covered_image_indices": [] if company and value is None else indices,
                    "coverage": "unreadable" if company and value is None else "complete",
                    ("reason" if company else "excerpt"): "受控测试未提供公司结尾事实" if company else "受控视觉事实",
                }
            )
        result[field] = observations
    return result


def _psw_rules() -> RuleInput:
    return RuleInput(
        rule_id="psw-cross-format",
        type=PpapDocumentType.PSW.get_type_name(),
        rules=[
            {
                "id": str(index),
                "category": f"规则{index}",
                "rule_to_check": f"规则{index}",
                "type_of_rule": ("image" if index in {3, 5, 7, 8, 9, 10} else "table"),
            }
            for index in range(1, 11)
        ],
    )


def _xlsx_key_fields(path: Path) -> dict[str, str]:
    audit = AuditInput(audit_id="key-fields", file_url=str(path))
    agent = PswExcelAgent(audit, _psw_rules())
    workbook = agent._load_workbook()
    try:
        ctx = agent.rule_ctx(workbook)
        table = Table(ctx)
        sheet = table.sheet("PSW主表")
        fields = table.field_block("PSW主表字段区")
        weight = str(fields.field("重量字段").value() or "").strip()
        supplier = agent._extract_supplier_name_code(sheet)
    finally:
        workbook.close()

    part_number = ""
    raw = load_workbook(path, data_only=True)
    try:
        for worksheet in raw.worksheets:
            for row in worksheet.iter_rows():
                for offset, cell in enumerate(row):
                    label = str(cell.value or "").replace("\n", "")
                    if "理想汽车零件号" not in label:
                        continue
                    for candidate in row[offset + 1 :]:
                        value = str(candidate.value or "").strip()
                        if value:
                            part_number = value
                            break
                    if part_number:
                        break
                if part_number:
                    break
            if part_number:
                break
    finally:
        raw.close()
    return {
        "part_number": part_number,
        "supplier": supplier,
        "weight": weight,
    }


def test_real_xlsx_pdf_pair_runs_extraction_and_audit_before_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    xlsx_path = _PAIR_ROOT.with_suffix(".xlsx")
    pdf_path = _PAIR_ROOT.with_suffix(".pdf")
    monkeypatch.setattr(
        "audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", lambda **request: _controlled_visual_facts(request["response_schema"], len(request["images"]), request["prompt"])
    )
    rules = _psw_rules()
    artifact = convert_pdf_to_excel_auto(FileRef(location=str(pdf_path)), enable_cache=True)
    xlsx_results = _run(PswExcelAgent, AuditInput(audit_id="paired-xlsx", file_url=str(xlsx_path)), rules)
    pdf_results = _run(PswPDFAgent, AuditInput(audit_id="paired-pdf", file_url=str(pdf_path)), rules)

    assert artifact is not None and artifact.success and artifact.xlsx_path
    reference = PswAuditSnapshot(
        source_format="xlsx",
        key_fields=_xlsx_key_fields(xlsx_path),
        results=tuple(xlsx_results),
    )
    candidate = PswAuditSnapshot(
        source_format="pdf",
        key_fields=_xlsx_key_fields(Path(artifact.xlsx_path)),
        results=tuple(pdf_results),
    )

    diff = compare_psw_snapshots(
        reference,
        candidate,
        allowed_format_rule_differences=frozenset({3, 5, 8, 9, 10}),
    )

    assert reference.key_fields
    assert all(reference.key_fields.values())
    assert diff.key_field_mismatches == ()
    assert diff.kind == DifferenceKind.CORRECT_FORMAT_DIFFERENCE, (
        diff,
        [(index, result.status, result.suggestions) for index, result in enumerate(xlsx_results, 1)],
        [(index, result.status, result.suggestions) for index, result in enumerate(pdf_results, 1)],
    )
    assert all(result.evidence is not None for result in xlsx_results + pdf_results)


def _li_psw_suite():
    """The 理想 PSW leaf with numeric clause aliases, as the comparison keys rules by index."""
    import ast
    import json

    from corpus_contracts.core_round import bind_suite, leaf_suite

    leaf = leaf_suite("li", PpapDocumentType.PSW.get_type_name())
    resource = ast.literal_eval(leaf.files["resources.py"].decode().split("RESOURCE = ", 1)[1])
    body = json.loads(leaf.files["rules.json"])
    rules = [{**row, "id": row["id"].rsplit(":", 1)[-1]} for row in body["rules"]]
    suite = bind_suite(leaf, {**body, "rules": rules}, {row["id"]: row["id"] for row in rules})
    return suite, resource["implementation_rules"]


def test_psw_xlsx_product_round_keeps_engine_statuses() -> None:
    from audit.audits import RoundInput, RoundWork
    from audit.rule_profile import RULE_PROFILE_V2
    from corpus_contracts.core_round import run_suite_round
    from corpus_contracts.product_round import (
        MATCH,
        compare_file,
    )

    xlsx_path = _PAIR_ROOT.with_suffix(".xlsx")
    doc_type = PpapDocumentType.PSW.get_type_name()
    import json

    suite, implementation_rules = _li_psw_suite()
    body = json.loads(suite.files["rules.json"])
    engine_results = _run(
        PswExcelAgent,
        AuditInput(audit_id="psw-xlsx-engine", file_url=str(xlsx_path)),
        RuleInput(rule_id="psw-xlsx", rule_profile=RULE_PROFILE_V2, type=doc_type, rules=implementation_rules),
    )
    # Rules 3 and 5 read the seal and signature visually; the direct agent call has no model replay,
    # so only the deterministic rules are compared here. The PDF test covers the visual rules.
    visual = {3, 5}
    expected = {index: str(getattr(result, "status", "") or "").upper() for index, result in enumerate(engine_results, 1) if index not in visual}
    work = RoundWork("psw-xlsx", "corpus", [RoundInput("target", "psw", "psw-version", xlsx_path.name, "ready", rule_version_id="psw-suite", rule_body=body)])
    result = run_suite_round(work, {"psw-version": xlsx_path.read_bytes()}, suites={"psw-version": suite})
    verdicts = compare_file(
        file_name=xlsx_path.name,
        version_id="psw-version",
        expected=expected,
        result=result,
        setup_error=None,
        expected_coverage="executed",
    )
    failures = [item for item in verdicts if item.kind != MATCH]
    assert not failures, failures


def test_psw_pdf_visual_rules_match_the_xlsx_product_round(model_replay) -> None:
    from audit.audits import RoundInput, RoundWork
    from corpus_contracts.core_round import run_suite_round
    from corpus_contracts.product_round import EXECUTION_FAILURE, MATCH, SETUP_FAILURE, compare_file
    from product.settings import get_settings

    settings = get_settings()
    if model_replay is None and not (settings.PPAP_MODEL_VISION_API_KEY.strip() and settings.PPAP_MODEL_VISION_BASE_URL.strip() and settings.PPAP_MODEL_VISION_MODEL.strip()):
        pytest.fail("setup_failure: dedicated OCR configuration is missing; PDF visual audit did not start")
    pdf_path = _PAIR_ROOT.with_suffix(".pdf")
    xlsx_path = _PAIR_ROOT.with_suffix(".xlsx")
    visual_rules = {3, 5, 7, 8, 9, 10}
    suite, _implementation_rules = _li_psw_suite()
    work = RoundWork("psw-xlsx-oracle", "corpus", [RoundInput("target", "psw", "psw-version", xlsx_path.name, "ready", rule_version_id="psw-suite")])
    oracle = run_suite_round(work, {"psw-version": xlsx_path.read_bytes()}, suites={"psw-version": suite})
    oracle_rows = compare_file(
        file_name=xlsx_path.name,
        version_id="psw-version",
        expected={index: "PASS" for index in visual_rules},
        result=oracle,
        setup_error=None,
        expected_coverage="executed",
    )
    expected = {}
    for item in oracle_rows:
        if item.kind in {SETUP_FAILURE, EXECUTION_FAILURE} or not item.actual:
            pytest.fail(f"xlsx oracle rule {item.rule_index}: {item.kind} {item.detail}")
        expected[item.rule_index] = item.actual
    work = RoundWork("psw-pdf-visual", "corpus", [RoundInput("target", "psw", "psw-version", pdf_path.name, "ready", rule_version_id="psw-suite")])
    result = run_suite_round(work, {"psw-version": pdf_path.read_bytes()}, suites={"psw-version": suite})
    verdicts = compare_file(
        file_name=pdf_path.name,
        version_id="psw-version",
        expected=expected,
        result=result,
        setup_error=None,
        expected_coverage="executed",
    )
    failures = [item for item in verdicts if item.kind != MATCH]
    assert not failures, failures

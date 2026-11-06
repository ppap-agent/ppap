from __future__ import annotations

import gc
import re
import shutil
import tempfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from audit.checkers.v2.psw_excel_agent import PswExcelAgent
from audit.contracts import (
    AuditInput,
    AuditResultItem,
    RuleInput,
)
from audit.engine import run_audit
from audit.engine.app_runtime import _AppAuditRuntime, run_audit_engine
from audit.engine.rule_engine import Table
from audit.kinds import PpapDocumentType
from corpus_contracts.psw_cross_format_regression import (
    DifferenceKind,
    PswAuditSnapshot,
    compare_psw_snapshots,
)
from documents.contracts import (
    ConversionArtifact,
    FileRef,
)
from documents.word import (
    WordTableBlock,
    convert_legacy_doc_to_docx,
    extract_docx_structure,
)
from openpyxl import load_workbook

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PAIR_ROOT = _REPO_ROOT / "assets" / "V2.0测试" / "模拟测试文件V2-01" / "16-零件提交保证书" / "16-零件提交保证书-上海亚大"
_LEGACY_DOC = _REPO_ROOT / "tests" / "corpus" / "fixtures" / "legacy_doc" / "psw.doc"


class _DeterministicRuntime(_AppAuditRuntime):
    def __init__(self) -> None:
        self.pdf_artifact: ConversionArtifact | None = None

    def convert_pdf_to_excel(
        self,
        file_ref: Any,
        *,
        enable_cache: bool = True,
    ) -> ConversionArtifact:
        artifact = ConversionArtifact.model_validate(
            super().convert_pdf_to_excel(
                file_ref,
                enable_cache=enable_cache,
            )
        )
        self.pdf_artifact = artifact
        return artifact

    def read_ocr_visual_facts(self, *, images, prompt, response_schema):
        return _controlled_visual_facts(response_schema, len(images), prompt)


def _controlled_visual_facts(response_schema, image_count, prompt=""):
    values = {"authorized_signature_present": True, "psw_stamp_present": True, "submission_any_checked": True}
    result = {}
    for field, schema in response_schema["schema"]["properties"].items():
        if "items" not in schema:  # A located region returns the bare value.
            result[field] = values.get(field)
            continue
        observations = []
        for scope in schema["items"]["properties"]["scope"]["enum"]:
            indices = [int(line.split(":", 1)[0].split()[1]) for line in prompt.splitlines() if line.startswith("图 ") and f"'scope': '{scope}'" in line] or list(range(image_count))
            observations.append(
                {
                    "scope": scope,
                    "value": values.get(field),
                    "sources": [{"image_index": indices[0], "bbox": [0, 0, 1, 1]}],
                    "covered_image_indices": indices,
                    "coverage": "complete",
                    "excerpt": "受控视觉事实",
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


def _word_key_fields(docx_content: bytes) -> dict[str, str]:
    document = extract_docx_structure(docx_content)
    text = " ".join(cell.text for block in document.blocks if isinstance(block, WordTableBlock) for cell in block.cells)

    def match(pattern: str) -> str:
        found = re.search(pattern, text)
        return found.group(1).strip() if found else ""

    return {
        "part_number": match(r"零件号[\s：:]*([A-Za-z0-9_-]+)"),
        "supplier": match(r"供应商名称/代码\s*([^\s]+)"),
        "weight": match(r"重量[\s：:]*([0-9]+(?:\.[0-9]+)?)"),
    }


def _core_results(results: list[Any]) -> tuple[AuditResultItem, ...]:
    return tuple(AuditResultItem.model_validate(result.model_dump()) for result in results)


def test_real_xlsx_pdf_pair_runs_extraction_and_audit_before_comparison() -> None:
    xlsx_path = _PAIR_ROOT.with_suffix(".xlsx")
    pdf_path = _PAIR_ROOT.with_suffix(".pdf")
    runtime = _DeterministicRuntime()
    rules = _psw_rules()
    artifact = runtime.convert_pdf_to_excel(
        FileRef(location=str(pdf_path)),
        enable_cache=True,
    )
    xlsx_results = run_audit(
        AuditInput(audit_id="paired-xlsx", file_url=str(xlsx_path)),
        rules,
        runtime=runtime,
    )
    pdf_results = run_audit(
        AuditInput(audit_id="paired-pdf", file_url=str(pdf_path)),
        rules,
        runtime=runtime,
    )

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


@pytest.fixture(params=["local", "http"])
def legacy_doc_source(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    if request.param == "local":
        yield str(_LEGACY_DOC)
        return

    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    handler = partial(SimpleHTTPRequestHandler, directory=str(_LEGACY_DOC.parent))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/psw.doc?sig=test"
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.parametrize("profile", ["v2", "v3"])
def test_real_doc_and_its_converted_docx_have_equivalent_results_and_release_temporary_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy_doc_source: str,
    profile: str,
) -> None:
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    assert executable is not None, "required PSW runtime must provide LibreOffice"
    monkeypatch.setenv("LEGACY_DOC_CONVERTER_EXECUTABLE", executable)
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled-ocr")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://ocr.invalid/v1")
    monkeypatch.setattr(
        "audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", lambda **request: _controlled_visual_facts(request["response_schema"], len(request["images"]), request["prompt"])
    )
    runtime_temp = tmp_path / "conversion-temporary"
    runtime_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(runtime_temp))
    original_bytes = _LEGACY_DOC.read_bytes()

    converted = convert_legacy_doc_to_docx(
        original_bytes,
        _LEGACY_DOC.name,
    )
    docx_path = tmp_path / "psw-converted.docx"
    docx_path.write_bytes(converted)
    rules = _psw_rules().model_copy(update={"rule_profile": profile})

    was_enabled = gc.isenabled()
    gc.disable()
    try:
        doc_results = run_audit_engine(
            AuditInput(audit_id="customer-doc", file_url=legacy_doc_source),
            rules,
        )
        assert list(runtime_temp.iterdir()) == []
        monkeypatch.setenv("LEGACY_DOC_CONVERTER_EXECUTABLE", str(tmp_path / "unavailable-converter"))
        docx_results = run_audit_engine(
            AuditInput(audit_id="converted-docx", file_url=str(docx_path)),
            rules,
        )
        assert list(runtime_temp.iterdir()) == []
    finally:
        if was_enabled:
            gc.enable()
        gc.collect()
    key_fields = _word_key_fields(converted)
    reference = PswAuditSnapshot(
        source_format="docx",
        key_fields=key_fields,
        results=_core_results(docx_results),
    )
    candidate = PswAuditSnapshot(
        source_format="doc",
        key_fields=_word_key_fields(converted),
        results=_core_results(doc_results),
    )

    diff = compare_psw_snapshots(reference, candidate)

    assert all(key_fields.values())
    assert diff.kind == DifferenceKind.EQUIVALENT
    assert all(result.evidence is not None for result in doc_results + docx_results)
    assert {result.evidence.source_format for result in doc_results if result.evidence} == {"doc"}
    assert {result.evidence.source_format for result in docx_results if result.evidence} == {"docx"}
    assert _LEGACY_DOC.read_bytes() == original_bytes
    assert docx_path.read_bytes() == converted


def test_psw_xlsx_product_round_keeps_engine_statuses() -> None:
    from audit.audits import RoundInput, RoundWork
    from audit.rule_profile import RULE_PROFILE_V2
    from audit.suite_migration import migrated_builtin_suite
    from corpus_contracts.core_round import run_suite_round
    from corpus_contracts.product_round import (
        MATCH,
        compare_file,
    )

    xlsx_path = _PAIR_ROOT.with_suffix(".xlsx")
    doc_type = PpapDocumentType.PSW.get_type_name()
    import json

    suite = migrated_builtin_suite(RULE_PROFILE_V2, doc_type)
    body = json.loads(suite.files["rules.json"])
    engine_results = run_audit(
        AuditInput(audit_id="psw-xlsx-engine", file_url=str(xlsx_path)),
        RuleInput(rule_id="psw-xlsx", rule_profile=RULE_PROFILE_V2, type=doc_type, rules=body["rules"]),
    )
    expected = {index: str(getattr(result, "status", "") or "").upper() for index, result in enumerate(engine_results, 1)}
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


def test_psw_pdf_visual_rules_match_the_xlsx_product_round() -> None:
    from audit.audits import RoundInput, RoundWork
    from audit.rule_profile import RULE_PROFILE_V2
    from audit.suite_migration import migrated_builtin_suite
    from corpus_contracts.core_round import run_suite_round
    from corpus_contracts.product_round import EXECUTION_FAILURE, MATCH, SETUP_FAILURE, compare_file
    from product.settings import get_settings

    settings = get_settings()
    if not (settings.OCR_OPENAI_API_KEY.strip() and settings.OCR_OPENAI_BASE_URL.strip() and settings.OCR_MODEL_NAME.strip()):
        pytest.fail("setup_failure: dedicated OCR configuration is missing; PDF visual audit did not start")
    pdf_path = _PAIR_ROOT.with_suffix(".pdf")
    xlsx_path = _PAIR_ROOT.with_suffix(".xlsx")
    doc_type = PpapDocumentType.PSW.get_type_name()
    visual_rules = {3, 5, 7, 8, 9, 10}
    suite = migrated_builtin_suite(RULE_PROFILE_V2, doc_type)
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

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from ppap_core.contracts import (
    AuditInput,
    AuditResultItem,
    ConversionArtifact,
    FileRef,
    RuleInput,
)
from ppap_core.types import PpapDocumentType
from ppap_docops.word import (
    WordTableBlock,
    convert_legacy_doc_to_docx,
    extract_docx_structure,
)
from ppap_excel_audit_engine import run_audit
from ppap_excel_audit_engine.app_runtime import _AppAuditRuntime
from ppap_excel_audit_engine.app_runtime import run_audit_engine
from ppap_excel_audit_engine.checkers.agents.psw_excel_agent import PswExcelAgent
from ppap_excel_audit_engine.rule_engine import Table
from tests.corpus.psw_cross_format_regression import (
    DifferenceKind,
    PswAuditSnapshot,
    compare_psw_snapshots,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
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

    def run_image_prompt(self, **_kwargs: Any) -> str:
        return json.dumps(
            {
                "classification": "视觉证据",
                "item": "PSW 视觉规则",
                "priority": "P0",
                "status": "PASS",
                "suggestions": "确定性回归视觉结果",
            }
        )


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
    assert diff.kind == DifferenceKind.CORRECT_FORMAT_DIFFERENCE
    assert all(result.evidence is not None for result in xlsx_results + pdf_results)


def test_real_local_doc_and_its_converted_docx_have_equivalent_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if executable is None:
        pytest.skip("optional local LibreOffice runtime is not installed")
    monkeypatch.setenv("LEGACY_DOC_CONVERTER_EXECUTABLE", executable)
    monkeypatch.setattr(
        _AppAuditRuntime,
        "run_image_prompt",
        _DeterministicRuntime.run_image_prompt,
    )

    converted = convert_legacy_doc_to_docx(
        _LEGACY_DOC.read_bytes(),
        _LEGACY_DOC.name,
    )
    docx_path = tmp_path / "psw-converted.docx"
    docx_path.write_bytes(converted)
    rules = _psw_rules()

    doc_results = run_audit_engine(
        AuditInput(audit_id="customer-doc", file_url=str(_LEGACY_DOC)),
        rules,
    )
    docx_results = run_audit_engine(
        AuditInput(audit_id="converted-docx", file_url=str(docx_path)),
        rules,
    )
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

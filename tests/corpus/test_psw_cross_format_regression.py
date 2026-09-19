from __future__ import annotations

from typing import Literal

from ppap_core.contracts import (
    AuditEvidence,
    AuditResultItem,
    EvidenceDiagnostic,
    EvidenceSource,
)
from tests.corpus.psw_cross_format_regression import (
    DifferenceKind,
    PswAuditSnapshot,
    compare_psw_snapshots,
)


def _result(
    source_format: Literal["xlsx", "pdf", "docx", "doc"],
    status: str,
    *,
    used: Literal["none", "structure"] = "structure",
    diagnostic: EvidenceDiagnostic | None = None,
) -> AuditResultItem:
    prepared_format: Literal["xlsx", "docx"]
    if source_format in {"doc", "docx"}:
        prepared_format = "docx"
    else:
        prepared_format = "xlsx"
    strategies = {
        "xlsx": ("native_excel",),
        "pdf": ("manual_linegrid",),
        "docx": ("docx_ooxml",),
        "doc": ("local_libreoffice_doc_to_docx", "docx_ooxml"),
    }[source_format]
    return AuditResultItem(
        classification="零件提交保证书",
        item="规则",
        priority="P1",
        status=status,
        suggestions="",
        evidence=AuditEvidence(
            required="structure",
            used=used,
            source_format=source_format,
            prepared_format=prepared_format,
            conversion_strategies=strategies,
            sources=(EvidenceSource(kind="structure", locator="fixture://psw/key-fields"),) if used == "structure" else (),
            diagnostics=(diagnostic,) if diagnostic is not None else (),
        ),
    )


def _snapshot(
    source_format: Literal["xlsx", "pdf", "docx", "doc"],
    statuses: tuple[str, ...],
    *,
    weight: str = "1.2345",
    diagnostic: EvidenceDiagnostic | None = None,
) -> PswAuditSnapshot:
    return PswAuditSnapshot(
        source_format=source_format,
        key_fields={
            "supplier": "示例总公司/S001",
            "part_number": "L123",
            "weight": weight,
        },
        results=tuple(_result(source_format, status, used="none" if diagnostic is not None else "structure", diagnostic=diagnostic) for status in statuses),
    )


def test_four_format_equivalence_compares_key_fields_and_rule_results() -> None:
    excel = _snapshot("xlsx", ("PASS", "PASS", "PASS"))
    pdf = _snapshot("pdf", ("PASS", "PASS", "PASS"))
    docx = _snapshot("docx", ("PASS", "PASS", "PASS"))
    doc = _snapshot("doc", ("PASS", "PASS", "PASS"))

    assert compare_psw_snapshots(excel, pdf).kind == DifferenceKind.EQUIVALENT
    assert compare_psw_snapshots(excel, docx).kind == DifferenceKind.EQUIVALENT
    assert compare_psw_snapshots(excel, doc).kind == DifferenceKind.EQUIVALENT


def test_explicit_rule_allowlist_classifies_a_correct_format_difference() -> None:
    excel = _snapshot("xlsx", ("PASS", "PASS", "PASS"))
    docx = _snapshot("docx", ("PASS", "PASS", "NG"))

    diff = compare_psw_snapshots(
        excel,
        docx,
        allowed_format_rule_differences=frozenset({3}),
    )

    assert diff.kind == DifferenceKind.CORRECT_FORMAT_DIFFERENCE
    assert diff.rule_status_mismatches == (3,)


def test_conversion_loss_is_not_relabelled_as_an_expected_difference() -> None:
    reference = _snapshot("xlsx", ("PASS", "PASS"))
    converted = _snapshot(
        "doc",
        ("NG", "NG"),
        weight="",
        diagnostic=EvidenceDiagnostic(
            code="psw.structure.insufficient",
            stage="structure",
            severity="error",
            message="DOCX 关键字段缺失。",
        ),
    )

    diff = compare_psw_snapshots(reference, converted)

    assert diff.kind == DifferenceKind.CONVERSION_LOSS
    assert diff.key_field_mismatches == ("weight",)
    assert diff.rule_status_mismatches == (1, 2)


def test_unexplained_status_change_is_an_audit_logic_regression() -> None:
    reference = _snapshot("xlsx", ("PASS", "PASS"))
    changed = _snapshot("pdf", ("NG", "PASS"))

    diff = compare_psw_snapshots(reference, changed)

    assert diff.kind == DifferenceKind.AUDIT_LOGIC_REGRESSION
    assert diff.rule_status_mismatches == (1,)

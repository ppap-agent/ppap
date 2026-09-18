"""Worker-side document identification. Never defaults a rule profile or project."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from ppap_core.drafts import (
    IDENT_AMBIGUOUS,
    IDENT_MATCHED,
    IDENT_UNCERTAIN,
    IDENT_UNRECOGNIZED,
    IDENT_UNSUPPORTED,
    MATCH_MIN_MARGIN,
    MATCH_MIN_SCORE,
    SUPPORTED_AUDIT_EXTENSIONS,
)
from ppap_core.files import original_filename
from ppap_core.types import PpapDocumentType


@dataclass(frozen=True)
class Identification:
    status: str
    matched_document_type: str | None
    proposed_engine_rule_id: str | None
    evidence: dict


def extract_content_signals(filename: str, body: bytes) -> str:
    name = original_filename(filename)
    ext = Path(name).suffix.lower()
    if ext not in {".xlsx", ".xlsm"}:
        return ""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return ""
    try:
        workbook = load_workbook(BytesIO(body), read_only=True, data_only=True)
    except Exception:
        return ""
    parts: list[str] = list(workbook.sheetnames)
    try:
        for sheet in workbook.worksheets:
            for index, row in enumerate(sheet.iter_rows(max_row=20, max_col=10, values_only=True)):
                for cell in row:
                    if cell is not None:
                        parts.append(str(cell)[:80])
                if index >= 19:
                    break
    finally:
        workbook.close()
    return " ".join(parts)[:4000]


def classify_file(filename: str, body: bytes) -> Identification:
    name = original_filename(filename)
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_AUDIT_EXTENSIONS:
        return Identification(
            IDENT_UNSUPPORTED,
            None,
            None,
            {
                "reason": "unsupported_format",
                "extension": ext or "none",
                "rule_profile": None,
                "candidates": [],
            },
        )
    content = extract_content_signals(name, body)
    matches = PpapDocumentType.rank_from_signals(filename=name, content=content)
    candidates = [
        {
            "document_type": item.doc_type.get_type_name(),
            "document_type_key": item.doc_type.name,
            "score": item.score,
            "alias_hits": list(item.alias_hits),
            "keyword_hits": list(item.keyword_hits),
        }
        for item in matches[:5]
    ]
    if not matches:
        return Identification(
            IDENT_UNRECOGNIZED,
            None,
            None,
            {"reason": "no_type_signal", "rule_profile": None, "candidates": []},
        )
    best = matches[0]
    second = matches[1] if len(matches) > 1 else None
    if best.score < MATCH_MIN_SCORE:
        return Identification(
            IDENT_UNCERTAIN,
            best.doc_type.get_type_name(),
            None,
            {
                "reason": "below_match_score",
                "score": best.score,
                "minimum_score": MATCH_MIN_SCORE,
                "rule_profile": None,
                "candidates": candidates,
            },
        )
    if second is not None and (best.score - second.score) < MATCH_MIN_MARGIN:
        return Identification(
            IDENT_AMBIGUOUS,
            None,
            None,
            {
                "reason": "insufficient_margin",
                "score": best.score,
                "next_score": second.score,
                "minimum_margin": MATCH_MIN_MARGIN,
                "rule_profile": None,
                "candidates": candidates,
            },
        )
    type_name = best.doc_type.get_type_name()
    return Identification(
        IDENT_MATCHED,
        type_name,
        type_name,
        {
            "reason": "unique_type_match",
            "score": best.score,
            "document_type_key": best.doc_type.name,
            "alias_hits": list(best.alias_hits),
            "keyword_hits": list(best.keyword_hits),
            "rule_profile": None,
            "candidates": candidates,
        },
    )

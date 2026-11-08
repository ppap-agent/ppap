"""PDF table reading and conversion. Same-version OCR policy stays in the reader."""

from __future__ import annotations

from typing import Any


def read_tables(source: Any, *, spec: Any = None) -> Any:
    from documents.pdf import extract_pdf_tables

    return extract_pdf_tables(source, spec=spec)


def convert_pdf_to_excel(source: Any, output_path: str, *, spec: Any = None) -> Any:
    from documents.pdf import convert_pdf_to_excel as convert

    return convert(source, output_path, spec=spec)

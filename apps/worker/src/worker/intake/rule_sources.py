"""Read rule citations from immutable original bytes, without model calls."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from product.rules import MAX_RULE_TEXT


def rule_sources(filename: str, content: bytes) -> list[dict[str, str]]:
    if len(content) > 25 * 1024 * 1024:
        raise ValueError("rule_source_too_large")
    extension = Path(filename).suffix.lower()
    if extension == ".doc":
        from documents.word.legacy import convert_legacy_doc_to_docx

        converted = rule_sources("original.docx", convert_legacy_doc_to_docx(content, filename))
        return [{**unit, "location": "转换原件 · " + unit["location"]} for unit in converted]
    if extension in {".docx", ".xlsx", ".xlsm"}:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 2048 or sum(entry.file_size for entry in entries) > 16 * 1024 * 1024:
                raise ValueError("rule_source_too_large")
    units: list[dict[str, str]] = []
    total = 0

    def add(identity: str, location: str, text: str) -> None:
        nonlocal total
        text = text.strip()
        if text:
            total += len(text)
            if total > MAX_RULE_TEXT or len(units) >= 4096:
                raise ValueError("rule_source_too_large")
            units.append({"id": identity, "location": location, "excerpt": text})

    if extension == ".docx":
        from documents.extractors.word import word_paragraph_text
        from docx import Document
        from docx.oxml.ns import qn

        doc = Document(BytesIO(content))
        for number, paragraph in enumerate(doc.paragraphs, 1):
            add(f"docx:p:{number}", f"段落 {number}", word_paragraph_text(paragraph._p))
        # Physical cells, not python-docx's expanded merged-cell aliases.
        for table_number, table in enumerate(doc.element.body.iter(qn("w:tbl")), 1):
            if any(ancestor.tag == qn("w:del") for ancestor in table.iterancestors()):
                continue  # Retain physical numbering without reviving deleted text.
            for row_number, row in enumerate(table.findall(qn("w:tr")), 1):
                for column, cell in enumerate(row.findall(qn("w:tc")), 1):
                    text = "\n".join(word_paragraph_text(paragraph) for paragraph in cell.findall(qn("w:p")))
                    add(f"docx:{table_number}:{row_number}:{column}", f"表 {table_number} · 行 {row_number} · 列 {column}", text)
    elif extension in {".xlsx", ".xlsm", ".xls"}:
        from xml.etree import ElementTree

        from documents.extractors import get_extractor

        root = ElementTree.fromstring("<DOCUMENT>" + get_extractor(extension).extract_text(content) + "</DOCUMENT>")
        for sheet in root.findall("SHEET"):
            name = sheet.attrib["name"]
            for cell in sheet.iter("CELL"):
                address = cell.attrib["address"]
                add(f"xlsx:{name}:{address}", f"{name} · {address}", cell.text or "")
    elif extension == ".pdf":
        import fitz

        with fitz.open(stream=content, filetype="pdf") as doc:
            if len(doc) > 500:
                raise ValueError("rule_source_too_large")
            for page_number, page in enumerate(doc, 1):
                for block in page.get_text("blocks"):
                    if block[6] == 0:
                        number = int(block[5]) + 1
                        add(f"pdf:{page_number}:{number}", f"第 {page_number} 页 · 区块 {number}", block[4])
    elif extension == ".json":
        body = json.loads(content.decode("utf-8-sig"))
        for number, rule in enumerate(body["rules"]):
            add(f"json:{number}", f"rules[{number}].rule_to_check", rule["rule_to_check"])
    elif extension == ".md":
        for number, line in enumerate(content.decode("utf-8-sig").splitlines(), 1):
            add(f"line:{number}", f"第 {number} 行", line)
    else:
        raise ValueError("rule_source_format_unavailable")
    if not units:
        raise ValueError("rule_source_text_unavailable")
    return units


def validate_registration_sources(context: dict, body: dict, version_id: str, filename: str, units: list[dict[str, str]]) -> None:
    """Registration must cite exact original units; free-form labels are not proof."""
    if "registration" not in context:
        return
    try:
        basis = json.loads(context["registration"])["basis"]
        clauses = basis["clauses"]
        by_id = {unit["id"]: unit for unit in units}
        applied = [clause for clause in clauses if clause.get("applicability", "apply") == "apply"]
        if basis["source"]["filename"] != filename or len(applied) != len(body["rules"]):
            raise ValueError()
        for clause in clauses:
            citation = clause["provenance"]
            unit = by_id[citation["id"]]
            if (
                clause["checked"] is not True
                or citation != {**unit, "fileVersionId": version_id}
                or clause["source"] != filename
                or clause["original"] != unit["excerpt"]
                or clause["location"] != unit["location"]
            ):
                raise ValueError()
        for clause, rule in zip(applied, body["rules"], strict=True):
            if any(clause[key].strip() != rule[field] for key, field in (("id", "id"), ("title", "category"), ("text", "rule_to_check"))):
                raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("rule_source_unverified") from None

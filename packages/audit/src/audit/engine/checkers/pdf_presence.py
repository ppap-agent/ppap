"""Whole-document existence over independently established page facts."""

import fitz

from audit.contracts import AuditInput


def read_pdf_presence(audit: AuditInput, *, field: str, description: str, witness, cache: dict, operation: str) -> bool | None:
    """Stop at a known witness; absence needs every original page to be false.

    Unknown page facts do not prove absence and do not defeat a later witness.
    Technical failures propagate. The reader owns paired observations and caches
    completed, unknown and failed requests; this loop never retries a page.
    """
    from audit.engine.checkers.visual_facts import read_original_pdf_visual_facts

    with fitz.open(audit.file_url) as document:
        count = len(document)
    if not count:
        return None
    unknown = False
    for number in range(1, count + 1):
        facts = read_original_pdf_visual_facts(audit, {field: description}, witness, operation=operation, cache=cache, source_pages=[number])
        value = facts.get(field)
        if value is True:
            return True
        unknown |= value is not False
    return None if unknown else False

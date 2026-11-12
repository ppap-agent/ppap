"""UAES template resources. Every alias is a spelling read from a UAES corpus original.

Each document module declares (sheets, sections, columns); a carrier combines the
documents its clauses read into one resource.
"""

from typing import Any

from documents.pdf import GroupingMode, GroupingSpec, OutputSpec, PdfSpec, StitchMode, StitchSpec

from audit.engine.rule_engine.contracts import RuleResource, RuleResourceModel, index_rule_resource

# Correctness first: search headers and scan rows over the whole used sheet, never a small
# window. A sheet formatted to its last row reports a near-infinite used range, so cap at 10,000.
WHOLE_SHEET_ROWS = 10_000
WHOLE_SHEET_COLS = 10_000

# Layouts every document can fall back to, after its own: one table over all pages,
# then each page on its own (the basic reading).
# Embedded pictures are placed on the converted sheets as well (the converter leaves this off),
# with no small count cap and no size floor: a stamp or a tick mark may be a tiny picture.
PICTURES: dict[str, Any] = {"preserve_pdf_embedded_images": True, "embedded_image_max_per_sheet": 10_000, "embedded_image_min_bytes": 0}
STITCHED = PdfSpec(grouping=GroupingSpec(mode=GroupingMode.ALL_PAGES), output=OutputSpec(**PICTURES))
PER_PAGE = PdfSpec(grouping=GroupingSpec(mode=GroupingMode.ALL_PAGES), stitch=StitchSpec(mode=StitchMode.NO_STITCH), output=OutputSpec(**PICTURES))


def combine(*documents) -> RuleResource:
    """One resource with the sheets, sections and columns of the documents a carrier reads.

    Documents may share a declaration (one PGQP1 sheet read by several carriers); a
    shared name must declare the same thing everywhere.
    """
    parts = []
    for index in range(3):
        merged: dict[str, Any] = {}
        for document in documents:
            for item in document[index]:
                if merged.setdefault(item.canonical_name, item) != item:
                    raise ValueError(f"conflicting UAES resource declaration: {item.canonical_name}")
        parts.append(tuple(merged.values()))
    return index_rule_resource(RuleResourceModel(sheets=parts[0], sections=parts[1], columns=parts[2]))

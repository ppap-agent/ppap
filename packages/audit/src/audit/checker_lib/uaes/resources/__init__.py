"""UAES template resources. Every alias is a spelling read from a UAES corpus original.

Each document module declares (sheets, sections, columns); a carrier combines the
documents its clauses read into one resource.
"""

from documents.pdf import GroupingMode, GroupingSpec, PdfSpec, StitchMode, StitchSpec

from audit.engine.rule_engine.contracts import RuleResourceModel, index_rule_resource

# Layouts every document can fall back to, after its own: one table over all pages,
# then each page on its own (the basic reading).
STITCHED = PdfSpec(grouping=GroupingSpec(mode=GroupingMode.ALL_PAGES))
PER_PAGE = PdfSpec(grouping=GroupingSpec(mode=GroupingMode.ALL_PAGES), stitch=StitchSpec(mode=StitchMode.NO_STITCH))


def combine(*documents) -> object:
    """One resource with the sheets, sections and columns of the documents a carrier reads.

    Documents may share a declaration (one PGQP1 sheet read by several carriers); a
    shared name must declare the same thing everywhere.
    """
    parts = []
    for index in range(3):
        merged = {}
        for document in documents:
            for item in document[index]:
                if merged.setdefault(item.canonical_name, item) != item:
                    raise ValueError(f"conflicting UAES resource declaration: {item.canonical_name}")
        parts.append(tuple(merged.values()))
    return index_rule_resource(RuleResourceModel(sheets=parts[0], sections=parts[1], columns=parts[2]))

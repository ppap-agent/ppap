"""Sample inspection report originals (U01, F-PU-207); aliases read from UAES corpus files."""

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

from . import PER_PAGE, STITCHED, WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

# The four reports, each on its own sheet: 尺寸报告dimension, 材料报告material, 功能报告function, 可靠性报告reliability.
REPORTS = {"尺寸报告": "dimension", "材料报告": "material", "功能报告": "function", "可靠性报告": "reliability"}

# Under "Evaluation" the next header row splits into OK and NOT OK columns.
SIGNATURE_MARKERS = ("Supplier responsible signature", "供应商负责人")


def _report(title: str, english: str):
    sheet = SheetModel(canonical_name=title, include_keywords=(title, english))
    section = SectionModel(
        canonical_name=f"{title}区",
        sheet_name=title,
        header_anchors=(f"{title}序号", f"{title}要求", f"{title}评价"),
        header_hit=2,
        header_search_max_row=WHOLE_SHEET_ROWS,
        header_search_max_col=WHOLE_SHEET_COLS,
        row_anchors=(f"{title}序号",),
        row_stop_on_first_blank=False,
        row_max_blank_streak=WHOLE_SHEET_ROWS,
        row_stop_markers=SIGNATURE_MARKERS,
    )
    columns = (
        ColumnModel(canonical_name=f"{title}序号", section_name=section.canonical_name, aliases=("No.",)),
        ColumnModel(canonical_name=f"{title}要求", section_name=section.canonical_name, aliases=("Requirements/要求",)),
        # 尺寸报告 heads one band "Actual-Value Supplier/供应商实测值/Actual-Value Customer 客户实测值";
        # the others split it into a supplier band and a customer band.
        ColumnModel(canonical_name=f"{title}供应商实测值", section_name=section.canonical_name, aliases=("Actual-Value Supplier/供应商实测值",)),
        ColumnModel(canonical_name=f"{title}评价", section_name=section.canonical_name, aliases=("Evaluation",)),
    )
    return sheet, section, columns


_PARTS = [_report(title, english) for title, english in REPORTS.items()]

DOCUMENT = (
    tuple(sheet for sheet, _section, _columns in _PARTS),
    tuple(section for _sheet, section, _columns in _PARTS),
    tuple(column for _sheet, _section, columns in _PARTS for column in columns),
)

PDF_SPECS = (STITCHED, PER_PAGE)

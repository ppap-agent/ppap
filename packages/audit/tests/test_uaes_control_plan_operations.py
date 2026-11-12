"""Control plan rows belong to the operation the drawn name column puts them under."""

from audit.checker_lib.uaes.control_plan import operation_owners
from audit.engine.rule_engine import RuleContext, Table
from audit.engine.rule_engine.contracts import RuleResourceModel, SheetModel, index_rule_resource
from openpyxl import Workbook
from openpyxl.styles import Border, Side


def _sheet(names, *, lines=(), merged=()):
    workbook = Workbook()
    ws = workbook.active
    ws.title = "CP"
    ws.append(["工序名称"])
    for name in names:
        ws.append([name])
    for row in lines:
        ws.cell(row, 1).border = Border(top=Side(style="thin"))
    for top, bottom in merged:
        ws.merge_cells(start_row=top, end_row=bottom, start_column=1, end_column=1)
    resource = index_rule_resource(RuleResourceModel(sheets=(SheetModel(canonical_name="计划", include_keywords=("cp",)),), sections=(), columns=()))
    ctx = RuleContext(None, workbook, resource, file_version_id="cp")
    return Table(ctx).sheet("计划")


def test_a_name_centred_in_an_unlined_block_owns_the_whole_block():
    # 776 operation 90: the name sits mid-block, nothing drawn above or below it.
    sheet = _sheet([None, None, "S700", None, None], lines=(2, 7))
    assert operation_owners(sheet, 1, range(2, 7), set()) == {row: ("S700",) for row in range(2, 7)}


def test_a_line_ends_a_block_so_a_blank_row_below_it_is_not_carried():
    sheet = _sheet(["熔化", None, None, "保温"], lines=(2, 4, 5))
    assert operation_owners(sheet, 1, range(2, 6), set()) == {2: ("熔化",), 3: ("熔化",), 4: (), 5: ("保温",)}


def test_rows_between_two_names_with_no_line_between_them_stay_open():
    # 776 operations 80/90: a merged name, then rows with no line before the next name.
    sheet = _sheet(["激光打标", None, None, None, "S700", None], lines=(2,), merged=((2, 3),))
    owners = operation_owners(sheet, 1, range(2, 8), set())
    assert owners[2] == owners[3] == ("激光打标",)
    assert owners[4] == owners[5] == ("激光打标", "S700")
    assert owners[6] == owners[7] == ("S700",)


def test_repeated_page_headings_are_not_names():
    sheet = _sheet(["清洗", "工序名称", None], lines=(2,))
    assert operation_owners(sheet, 1, range(2, 5), {3}) == {2: ("清洗",), 3: ("清洗",), 4: ("清洗",)}

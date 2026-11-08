"""U08 guest execution. The audit distribution does not install agent."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from audit.checker_core import checker_digest
from audit.standard_suites import load_predefined_checkers, predefined_directory_suites

CHECKER = "def create_audit(audit_file, rule, group_context=None):\n    return None\n"
RULES = {"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {"C1": "checker:create_audit"}}


def write_leaf(root: Path, source_path: str, *, checker: str = CHECKER, rules: dict | bytes | None = None, extra: dict[str, bytes] | None = None) -> Path:
    directory = root.joinpath(*source_path.split("/"))
    directory.mkdir(parents=True)
    (directory / "checker.py").write_text(checker, encoding="utf-8")
    body = json.dumps(RULES if rules is None else rules).encode() if not isinstance(rules, bytes) else rules
    (directory / "rules.json").write_bytes(body)
    for name, content in (extra or {}).items():
        (directory / name).write_bytes(content)
    return directory


def test_u08_bound_clauses_read_xlsx_and_do_not_pass_without_evidence(tmp_path):
    suite = next(item for item in predefined_directory_suites() if item.source_path == "v5/UAES/U08")
    pdf = tmp_path / "plan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    unable = _run(suite, pdf, ("U08:1", "U08:4"))
    assert [item.status for item in unable] == ["UNABLE", "UNABLE"]
    assert {item.suggestions for item in unable} == {"该预置 Checker 不支持此原件载体。"}

    passed = _run(suite, _workbook(tmp_path / "ready.xlsx", resource="压机", mark="CC"), ("U08:1", "U08:4"))
    assert [item.status for item in passed] == ["PASS", "PASS"]
    assert all(item.evidence and item.evidence.sources for item in passed)
    assert all(locator["file_version_id"] == "frozen-version" and locator["kind"] == "xlsx_cell" for item in passed for locator in _locators(item))
    failed = _run(suite, _workbook(tmp_path / "gap.xlsx", resource="", mark=""), ("U08:1", "U08:4"))
    assert [item.status for item in failed] == ["NG", "NG"]
    assert all(locator["file_version_id"] == "frozen-version" and locator["kind"] == "xlsx_cell" for item in failed for locator in _locators(item))
    missing = _run(suite, _workbook(tmp_path / "plain.xlsx", headers=("名称",), resource="x", mark="x"), ("U08:1", "U08:4"))
    assert [item.status for item in missing] == ["NG", "NG"]
    for item in missing:
        locators = _locators(item)
        assert locators and {locator["kind"] for locator in locators} == {"xlsx_range"}
        assert all(locator["file_version_id"] == "frozen-version" for locator in locators)
        assert all(locator.get("cell") != "A1" for locator in locators)
        assert [diag.code for diag in item.evidence.diagnostics] == ["column_not_in_sheet"]
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not a workbook")
    unread = _run(suite, broken, ("U08:1",))
    assert unread[0].status == "UNABLE"
    assert unread[0].status != "PASS"


def test_execution_uses_frozen_bytes_after_the_source_directory_changes(tmp_path):
    checker = (
        "from audit.contracts import AuditResultItem\n"
        "TOKEN = 'ORIGINAL'\n"
        "class _Check:\n"
        "    def __init__(self):\n"
        "        self.rule_item = type('Rule', (), {'id': 'C1'})()\n"
        "    def check(self):\n"
        "        return AuditResultItem(classification='x', item='x', priority='P0', status='NG', suggestions=TOKEN, clause_id='C1')\n"
        "def create_audit(audit_file, rule, group_context=None):\n"
        "    return type('Audit', (), {'checks': [_Check()], 'close': lambda self: None})()\n"
    )
    write_leaf(tmp_path, "v5/UAES/U08", checker=checker)
    frozen = dict(load_predefined_checkers(tmp_path)[0].files)
    path = tmp_path / "v5" / "UAES" / "U08" / "checker.py"
    path.write_text(checker.replace("ORIGINAL", "LIVE"), encoding="utf-8")
    assert b"LIVE" in load_predefined_checkers(tmp_path)[0].files["checker.py"]
    target = tmp_path / "ignored.xlsx"
    target.write_bytes(b"unused")
    result = _execute(frozen, target, ["C1"])
    assert result[0].suggestions == "ORIGINAL"
    assert result[0].status == "NG"


def _workbook(path: Path, *, headers=("工序名称/操作描述", "机器/装置/夹具/工装", "特性类型"), resource="压机", mark="CC") -> Path:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "控制计划"
    sheet.append(["控制计划"])
    sheet.append(list(headers))
    sheet.append(["装配", resource, mark])
    book.save(path)
    book.close()
    return path


def _run(suite, path: Path, clauses: tuple[str, ...]):
    return _execute(dict(suite.files), path, clauses)


def _locators(item):
    return [json.loads(source.locator) for source in item.evidence.sources]


def _view(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _view(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_view(item) for item in value)
    return value


def _save(path: Path, build) -> Path:
    from openpyxl import Workbook

    book = Workbook()
    build(book)
    book.save(path)
    book.close()
    return path


def _execute(files: dict[str, bytes], path: Path, clauses):
    """Run frozen program bytes through the official guest entry."""
    from agent.execution.audit_runtime import run_audit_process
    from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding

    body = path.read_bytes()
    rules = json.loads(files["rules.json"])
    program = {name: files[name] for name in ("checker.py", "resources.py") if name in files}
    digest = checker_digest(program)
    declared = rules.get("checker_binding") or {clause: "checker:create_audit" for clause in clauses}
    binding = {clause: declared[clause] for clause in clauses}
    source = FrozenInput("frozen-version", hashlib.sha256(body).hexdigest(), "target", path.name)
    target = FrozenTarget("frozen-version", "revision", "predefined:test", "a" * 64, rules, RuleBinding("predefined:test", digest, binding))
    snapshot = ExecutionSnapshot("tenant", "initiator", (source,), (target,), "runtime", "library", {})
    result = run_audit_process(snapshot, {"frozen-version": body}, {digest: program})
    by_clause = {row["clause_id"]: row for row in result["findings"]}
    return [_view(by_clause[clause]) for clause in clauses]


def test_incomplete_scan_formula_merge_and_extra_tables_are_unable(tmp_path):
    suite = next(item for item in predefined_directory_suites() if item.source_path == "v5/UAES/U08")
    files = dict(suite.files)

    def run(path: Path, clauses=("U08:1", "U08:4")):
        return _execute(files, path, clauses)

    def unable(path: Path, code: str, clause: str = "U08:1"):
        item = run(path, (clause,))[0]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert item.status == "UNABLE"
        assert [diag.code for diag in item.evidence.diagnostics] == [code]
        locators = _locators(item)
        assert locators
        assert all(locator["file_version_id"] == "frozen-version" and locator["source_sha256"] == digest for locator in locators)
        assert all(locator["kind"] == "xlsx_range" for locator in locators) if code != "formula_cache_missing" else True
        return locators

    def headers(sheet, row=1):
        sheet.cell(row, 1, "工序名称/操作描述")
        sheet.cell(row, 2, "机器/装置/夹具/工装")
        sheet.cell(row, 3, "特性类型")

    row_limit = _save(
        tmp_path / "row-limit.xlsx",
        lambda book: (
            setattr(book.active, "title", "控制计划"),
            book.active.cell(41, 1, "工序名称/操作描述"),
            book.active.cell(41, 2, "机器/装置/夹具/工装"),
            book.active.cell(42, 1, "装配"),
            book.active.cell(42, 2, "压机"),
        ),
    )
    located = unable(row_limit, "scan_incomplete")
    assert located[0]["end"] == "B40"
    assert run(row_limit)[1].status == "UNABLE"

    col_limit = _save(
        tmp_path / "col-limit.xlsx",
        lambda book: (
            setattr(book.active, "title", "控制计划"),
            book.active.cell(1, 31, "工序名称/操作描述"),
            book.active.cell(1, 32, "机器/装置/夹具/工装"),
            book.active.cell(2, 31, "装配"),
            book.active.cell(2, 32, "压机"),
        ),
    )
    assert unable(col_limit, "scan_incomplete")[0]["end"] == "AD2"

    def wide(book):
        sheet = book.active
        sheet.title = "控制计划"
        headers(sheet)
        sheet.cell(2, 1, "装配")
        sheet.cell(2, 2, "压机")
        sheet.cell(2, 3, "CC")
        sheet.cell(1, 31, "备注")

    assert unable(_save(tmp_path / "wide.xlsx", wide), "scan_incomplete")[0]["end"] == "AD2"
    assert run(_save(tmp_path / "wide.xlsx", wide), ("U08:1",))[0].status != "PASS"

    def fill_past_data_window(book):
        sheet = book.active
        sheet.title = "控制计划"
        headers(sheet)
        for row in range(2, 202):
            sheet.cell(row, 1, "装配")
            sheet.cell(row, 2, "压机")
            sheet.cell(row, 3, "CC")
        sheet.cell(202, 1, "装配")

    past = _save(tmp_path / "past-data.xlsx", fill_past_data_window)
    past_range = unable(past, "scan_incomplete")
    assert past_range[0]["end"] == "B201"
    assert run(past, ("U08:4",))[0].status == "UNABLE"

    formula = _save(
        tmp_path / "formula.xlsx",
        lambda book: (
            setattr(book.active, "title", "控制计划"),
            headers(book.active),
            book.active.cell(2, 1, "装配"),
            book.active.cell(2, 2, "=A2"),
            book.active.cell(2, 3, "CC"),
        ),
    )
    formula_digest = hashlib.sha256(formula.read_bytes()).hexdigest()
    assert unable(formula, "formula_cache_missing")[0] == {"kind": "xlsx_cell", "sheet": "控制计划", "cell": "B2", "file_version_id": "frozen-version", "source_sha256": formula_digest}

    hidden_header = _save(
        tmp_path / "formula-header.xlsx",
        lambda book: (
            setattr(book.active, "title", "控制计划"),
            book.active.cell(1, 1, '="工序名称/操作描述"'),
            book.active.cell(1, 2, "机器/装置/夹具/工装"),
            book.active.cell(2, 1, "装配"),
            book.active.cell(2, 2, "压机"),
        ),
    )
    assert unable(hidden_header, "formula_cache_missing")[0]["cell"] == "A1"
    assert run(hidden_header, ("U08:1",))[0].status != "NG"

    errored = _save(
        tmp_path / "error.xlsx",
        lambda book: (setattr(book.active, "title", "控制计划"), book.active.cell(1, 1, "特性类型"), book.active.cell(2, 1, "#N/A")),
    )
    assert unable(errored, "formula_cache_missing", "U08:4")[0]["cell"] == "A2"

    def complete(sheet):
        sheet.title = "控制计划"
        headers(sheet, 2)
        sheet.cell(1, 1, "控制计划")
        sheet.cell(3, 1, "装配")
        sheet.cell(3, 2, "压机")
        sheet.cell(3, 3, "CC")

    blank_extra = _save(
        tmp_path / "blank-extra.xlsx",
        lambda book: (complete(book.active), book.create_sheet("封面"), book.create_sheet("图例")),
    )
    assert [item.status for item in run(blank_extra)] == ["PASS", "PASS"]

    def legend_and_plan(book):
        plan = book.active
        plan.title = "控制计划"
        plan.cell(1, 1, "特性类型")
        legend = book.create_sheet("图例")
        legend.cell(1, 1, "特性类型")
        legend.cell(2, 1, "CC")

    legend = unable(_save(tmp_path / "legend.xlsx", legend_and_plan), "legend_or_multiple_tables", "U08:4")
    assert {locator["kind"] for locator in legend} == {"xlsx_range"}

    def two_tables(book):
        complete(book.active)
        other = book.create_sheet("过程")
        headers(other)
        other.cell(2, 1, "装配")
        other.cell(2, 2, "压机")
        other.cell(2, 3, "CC")

    tables = unable(_save(tmp_path / "two.xlsx", two_tables), "legend_or_multiple_tables")
    assert len(tables) == 2 and {locator["kind"] for locator in tables} == {"xlsx_range"}

    def merged_data(book):
        sheet = book.active
        sheet.title = "控制计划"
        headers(sheet)
        sheet.cell(2, 1, "装配")
        sheet.cell(2, 2, "压机")
        sheet.cell(3, 1, "检验")
        sheet.merge_cells("B2:B3")

    assert unable(_save(tmp_path / "merged.xlsx", merged_data), "merged_range")[0]["kind"] == "xlsx_range"

    def title_merge(book):
        sheet = book.active
        complete(sheet)
        sheet.merge_cells("A1:C1")

    assert [item.status for item in run(_save(tmp_path / "title.xlsx", title_merge))] == ["PASS", "PASS"]

    def outside_merge(book):
        sheet = book.active
        headers(sheet)
        sheet.cell(2, 1, "装配")
        sheet.cell(2, 2, "压机")
        sheet.cell(2, 3, "CC")
        sheet.cell(2, 4, "备注")
        sheet.merge_cells("D2:D3")

    assert [item.status for item in run(_save(tmp_path / "outside.xlsx", outside_merge))] == ["PASS", "PASS"]

    def merged_title_without_column(book):
        sheet = book.active
        sheet.title = "控制计划"
        sheet.cell(1, 1, "标题")
        sheet.merge_cells("A1:C1")
        sheet.cell(2, 1, "名称")

    assert unable(_save(tmp_path / "merged-title.xlsx", merged_title_without_column), "scan_incomplete")[0]["kind"] == "xlsx_range"

    empty = _save(tmp_path / "empty.xlsx", lambda book: setattr(book.active, "title", "控制计划"))
    blank = run(empty, ("U08:1",))[0]
    assert blank.status == "NG" and blank.evidence.used == "none" and blank.evidence.sources == ()

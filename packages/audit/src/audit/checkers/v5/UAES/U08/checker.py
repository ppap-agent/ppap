"""UAES control-plan checks owned by this directory leaf.

Only clauses listed in resources.RESOURCE are executable. A carrier this leaf
does not read is UNABLE. A sheet this leaf did not read completely is UNABLE.
It does not call another checker version.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from audit.contracts import AuditEvidence, AuditResultItem, EvidenceDiagnostic, EvidenceSource
from documents.excel import ExcelLoader, ExcelLoadOptions, load_excel_workbook
from openpyxl.utils import get_column_letter
from resources import RESOURCE

_HEADER_ROWS = 40
_HEADER_COLS = 30
_DATA_ROWS = 200
_UNSUPPORTED = "该预置 Checker 不支持此原件载体。"
_UNREADABLE = "无法读取控制计划工作簿，不能判定本条款。"
_INCOMPLETE = "已核范围没有覆盖完整表格，不能判定本条款。"
_FORMULA = "单元格公式没有缓存值，不能判定本条款。"
_MERGED = "合并单元格与待核列重叠，不能判定本条款。"
_AMBIGUOUS = "存在多张业务表或图例表，不能判定本条款。"
_LEGEND = ("图例", "填写说明")


@dataclass(frozen=True)
class _Sheet:
    kind: str
    sheet: str = ""
    status: str = "UNABLE"
    suggestion: str = ""
    cells: tuple[str, ...] = ()
    scanned: tuple[str, str] | None = None
    code: str = ""
    message: str = ""


def create_audit(audit_file, rule, group_context=None):
    return _ControlPlanAudit(audit_file, rule, group_context)


class _Clause:
    def __init__(self, audit: _ControlPlanAudit, clause_id: str):
        self.rule_item = type("_RuleItem", (), {"id": clause_id})()
        self._audit = audit
        self._clause_id = clause_id

    def check(self) -> AuditResultItem:
        return self._audit.evaluate(self._clause_id)


class _ControlPlanAudit:
    def __init__(self, audit_file, rule, group_context):
        self.audit_file = audit_file
        self.rule = rule
        self._group_context = group_context
        self._book = None
        self._formulas = None
        self._unreadable = False
        self.checks = [_Clause(self, clause_id) for clause_id in sorted(RESOURCE["clauses"])]

    def close(self) -> None:
        for book in (self._book, self._formulas):
            if book is not None:
                book.close()
        self._book = None
        self._formulas = None

    def evaluate(self, clause_id: str) -> AuditResultItem:
        suffix = Path(self.audit_file.file_url).suffix.lower()
        if suffix not in RESOURCE["supported_suffixes"]:
            return self._item(clause_id, "UNABLE", _UNSUPPORTED, checks_performed=0)
        if not self._version():
            return self._item(clause_id, "UNABLE", _UNREADABLE, checks_performed=0, evidence=self._diagnostic("frozen_file_missing", _UNREADABLE, ()))
        opened = self._open()
        if opened is None:
            return self._item(clause_id, "UNABLE", _UNREADABLE, checks_performed=0)
        spec = RESOURCE["clauses"][clause_id]
        if "resource_headers" in spec:
            return self._resources(clause_id, suffix, spec)
        return self._marks(clause_id, suffix, spec)

    def _open(self):
        if self._book is None and not self._unreadable:
            try:
                self._book = load_excel_workbook(str(self.audit_file.file_url))
                self._formulas = ExcelLoader.load_workbook(str(self.audit_file.file_url), ExcelLoadOptions(data_only=False))
            except Exception:
                self._unreadable = True
                self.close()
                self._unreadable = True
        if self._unreadable or self._formulas is None:
            return None
        if [sheet.title for sheet in self._book.worksheets] != [sheet.title for sheet in self._formulas.worksheets]:
            self._unreadable = True
            self.close()
            self._unreadable = True
            return None
        return self._book

    def _resources(self, clause_id: str, suffix: str, spec: dict) -> AuditResultItem:
        return self._decide(
            clause_id,
            suffix,
            {"operation": spec["operation_headers"], "resource": spec["resource_headers"]},
            "resource",
            "控制计划没有生产资源列。",
            "控制计划生产资源列没有内容。",
            "控制计划存在未填写生产资源的工序。",
            "已读取各工序对应的生产资源。",
        )

    def _marks(self, clause_id: str, suffix: str, spec: dict) -> AuditResultItem:
        return self._decide(
            clause_id,
            suffix,
            {"mark": spec["mark_headers"]},
            "mark",
            "控制计划没有特性类型或特殊特性列。",
            "特性类型或特殊特性列没有特性标记。",
            "特性类型或特殊特性列没有特性标记。",
            "已读取特性类型或特殊特性列中的特性标记。",
        )

    def _decide(self, clause_id: str, suffix: str, aliases: dict[str, tuple[str, ...]], required: str, missing: str, empty: str, gap: str, passed: str) -> AuditResultItem:
        formulas = {sheet.title: sheet for sheet in self._formulas.worksheets}
        tables: list[_Sheet] = []
        blockers: list[_Sheet] = []
        scanned: list[_Sheet] = []
        for sheet in self._book.worksheets:
            outcome = _inspect(sheet, formulas[sheet.title], aliases, required, missing, empty, gap, passed)
            if outcome.kind == "table":
                tables.append(outcome)
            elif outcome.kind == "absent":
                scanned.append(outcome)
            else:
                blockers.append(outcome)
        if len(tables) > 1:
            for table in tables:
                blockers.append(_Sheet("block", table.sheet, suggestion=_AMBIGUOUS, message=_AMBIGUOUS, code="legend_or_multiple_tables", scanned=table.scanned))
        if blockers:
            return self._item(clause_id, "UNABLE", blockers[0].suggestion or blockers[0].message, evidence=self._blocked(suffix, blockers))
        if len(tables) == 1:
            table = tables[0]
            evidence = self._cells(suffix, table.sheet, table.cells)
            if table.status == "PASS" and not evidence.sources:
                return self._item(clause_id, "UNABLE", _UNREADABLE, checks_performed=1)
            return self._item(clause_id, table.status, table.suggestion, evidence=evidence)
        ranges = tuple(item for item in scanned if item.scanned)
        evidence = self._ranges(suffix, ranges, "column_not_in_sheet", missing)
        return self._item(clause_id, "NG", missing, evidence=evidence)

    def _blocked(self, suffix: str, blockers: list[_Sheet]) -> AuditEvidence:
        sources: list[EvidenceSource] = []
        diagnostics: list[EvidenceDiagnostic] = []
        seen: set[str] = set()
        for item in blockers:
            if item.code and item.code not in seen:
                seen.add(item.code)
                diagnostics.append(EvidenceDiagnostic(code=item.code, stage="structure", severity="warning", message=item.message or item.suggestion))
            if item.cells:
                sources.extend(self._cell_source(suffix, item.sheet, cell) for cell in item.cells[:8])
            elif item.scanned:
                sources.append(self._range_source(suffix, item.sheet, item.scanned[0], item.scanned[1]))
        return self._evidence(suffix, tuple(sources[:8]), tuple(diagnostics))

    def _cells(self, suffix: str, sheet: str, cells: tuple[str, ...]) -> AuditEvidence:
        return self._evidence(suffix, tuple(self._cell_source(suffix, sheet, cell) for cell in cells[:8]), ())

    def _ranges(self, suffix: str, sheets: tuple[_Sheet, ...], code: str, message: str) -> AuditEvidence:
        sources = tuple(self._range_source(suffix, item.sheet, item.scanned[0], item.scanned[1]) for item in sheets[:8] if item.scanned)
        diagnostic = (EvidenceDiagnostic(code=code, stage="structure", severity="warning", message=message),) if code else ()
        return self._evidence(suffix, sources, diagnostic)

    def _cell_source(self, suffix: str, sheet: str, cell: str) -> EvidenceSource:
        return EvidenceSource(kind="structure", locator=self._locator({"kind": "xlsx_cell", "sheet": sheet, "cell": cell}))

    def _range_source(self, suffix: str, sheet: str, start: str, end: str) -> EvidenceSource:
        return EvidenceSource(kind="structure", locator=self._locator({"kind": "xlsx_range", "sheet": sheet, "start": start, "end": end}))

    def _evidence(self, suffix: str, sources: tuple[EvidenceSource, ...], diagnostics: tuple[EvidenceDiagnostic, ...]) -> AuditEvidence:
        kind = _carrier(suffix)
        return AuditEvidence(
            required="structure",
            used="structure" if sources else "none",
            source_format=kind,
            prepared_format=kind,
            sources=sources,
            diagnostics=diagnostics,
        )

    def _diagnostic(self, code: str, message: str, sources: tuple[EvidenceSource, ...]) -> AuditEvidence:
        return AuditEvidence(
            required="structure",
            used="none",
            source_format="xlsx",
            prepared_format="xlsx",
            sources=sources,
            diagnostics=(EvidenceDiagnostic(code=code, stage="structure", severity="warning", message=message),),
        )

    def _locator(self, payload: dict[str, str]) -> str:
        body = dict(payload)
        body["file_version_id"] = self._version()
        digest = self._sha()
        if digest:
            body["source_sha256"] = digest
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))

    def _version(self) -> str:
        meta = self.audit_file.file_metadata if isinstance(getattr(self.audit_file, "file_metadata", None), dict) else {}
        value = meta.get("file_version_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        audit_id = getattr(self.audit_file, "audit_id", "")
        return audit_id.strip() if isinstance(audit_id, str) else ""

    def _sha(self) -> str:
        meta = self.audit_file.file_metadata if isinstance(getattr(self.audit_file, "file_metadata", None), dict) else {}
        value = meta.get("sha256")
        return value if isinstance(value, str) and len(value) == 64 else ""

    def _item(self, clause_id: str, status: str, suggestion: str, *, evidence: AuditEvidence | None = None, checks_performed: int = 1) -> AuditResultItem:
        category = clause_id
        for row in getattr(self.rule, "rules", ()) or ():
            if isinstance(row, dict) and row.get("id") == clause_id:
                category = str(row.get("category") or clause_id)
                break
        return AuditResultItem(
            classification="规则审核",
            item=category,
            priority="P0",
            status=status,
            suggestions=suggestion,
            evidence=evidence,
            clause_id=clause_id,
            checks_performed=checks_performed,
        )


def _inspect(sheet, formula_sheet, aliases: dict[str, tuple[str, ...]], required: str, missing: str, empty: str, gap: str, passed: str) -> _Sheet:
    max_row, max_col = int(sheet.max_row or 1), int(sheet.max_column or 1)
    found = _headers(sheet, aliases)
    columns = found[1] if found else {}
    header_row = found[0] if found else None
    header_found = required in columns and header_row is not None
    if not header_found:
        limit_row, limit_col = min(max_row, _HEADER_ROWS), min(max_col, _HEADER_COLS)
        if max_row > _HEADER_ROWS or max_col > _HEADER_COLS or _window_has_merge(sheet, limit_row, limit_col):
            return _Sheet("block", sheet.title, suggestion=_INCOMPLETE, message=_INCOMPLETE, code="scan_incomplete", scanned=_window(1, limit_row, 1, limit_col))
    else:
        assert header_row is not None
        last_row = header_row + _DATA_ROWS
        if max_col > _HEADER_COLS:
            return _Sheet("block", sheet.title, suggestion=_INCOMPLETE, message=_INCOMPLETE, code="scan_incomplete", scanned=_window(header_row, min(max_row, last_row), 1, _HEADER_COLS))
        if _legend(sheet.title):
            window = _window(header_row, min(max_row, last_row), 1, min(max_col, _HEADER_COLS))
            return _Sheet("block", sheet.title, suggestion=_AMBIGUOUS, message=_AMBIGUOUS, code="legend_or_multiple_tables", scanned=window)
        if max_row > last_row:
            relevant = tuple(columns.values())
            return _Sheet("block", sheet.title, suggestion=_INCOMPLETE, message=_INCOMPLETE, code="scan_incomplete", scanned=_window(header_row, last_row, min(relevant), max(relevant)))
    hidden = _uncached_cell(sheet, formula_sheet, max_row, min(max_col, _HEADER_COLS))
    if hidden:
        return _Sheet("block", sheet.title, suggestion=_FORMULA, message=_FORMULA, code="formula_cache_missing", cells=(hidden,))
    if not header_found:
        covered = _window(1, max_row, 1, max_col) if _nonempty(sheet, max_row, max_col) else None
        return _Sheet("absent", sheet.title, scanned=covered)
    assert header_row is not None
    last_row = header_row + _DATA_ROWS
    relevant = tuple(columns.values())
    if _table_has_merge(sheet, header_row, last_row, relevant):
        return _Sheet("block", sheet.title, suggestion=_MERGED, message=_MERGED, code="merged_range", scanned=_window(header_row, max_row, min(relevant), max(relevant)))
    reads: dict[tuple[int, int], Any] = {}
    for row in range(header_row, max_row + 1):
        for col in relevant:
            reads[(row, col)] = _cached(sheet, formula_sheet, row, col)
    judged = _judge(sheet, header_row, max_row, columns, required, reads, empty, gap, passed)
    window = _window(header_row, max_row, min(relevant), max(relevant))
    return _Sheet(judged.kind, judged.sheet, judged.status, judged.suggestion, judged.cells, window, judged.code, judged.message)


def _judge(sheet, header_row: int, max_row: int, columns: dict[str, int], required: str, reads: dict[tuple[int, int], Any], empty: str, gap: str, passed: str) -> _Sheet:
    if required == "mark":
        col = columns["mark"]
        marked = [sheet.cell(row, col).coordinate for row in range(header_row + 1, max_row + 1) if not _blank(reads[(row, col)])]
        if marked:
            return _Sheet("table", sheet.title, "PASS", passed, tuple(marked))
        return _Sheet("table", sheet.title, "NG", empty, (sheet.cell(header_row, col).coordinate,))
    resource_col = columns["resource"]
    operation_col = columns.get("operation")
    gaps: list[str] = []
    filled: list[str] = []
    for row in range(header_row + 1, max_row + 1):
        resource = reads[(row, resource_col)]
        if operation_col is None:
            if not _blank(resource):
                filled.append(sheet.cell(row, resource_col).coordinate)
            continue
        if _blank(reads[(row, operation_col)]):
            continue
        if _blank(resource):
            gaps.append(sheet.cell(row, operation_col).coordinate)
        else:
            filled.append(sheet.cell(row, resource_col).coordinate)
    if gaps:
        return _Sheet("table", sheet.title, "NG", gap, tuple(gaps))
    if filled:
        return _Sheet("table", sheet.title, "PASS", passed, tuple(filled))
    return _Sheet("table", sheet.title, "NG", empty, (sheet.cell(header_row, resource_col).coordinate,))


def _headers(sheet, aliases: dict[str, tuple[str, ...]]):
    best = None
    limit_row = min(int(sheet.max_row or 1), _HEADER_ROWS)
    limit_col = min(int(sheet.max_column or 1), _HEADER_COLS)
    for row in range(1, limit_row + 1):
        found: dict[str, int] = {}
        for col in range(1, limit_col + 1):
            for role, names in aliases.items():
                if role not in found and _matches(sheet.cell(row, col).value, names):
                    found[role] = col
        if found and (best is None or len(found) > len(best[1])):
            best = (row, found)
            if len(found) == len(aliases):
                break
    return best


def _uncached_cell(sheet, formula_sheet, max_row: int, max_col: int) -> str | None:
    for row in range(1, max_row + 1):
        for col in range(1, max_col + 1):
            value = _cached(sheet, formula_sheet, row, col)
            if value == "uncached" or value == "error":
                return str(sheet.cell(row, col).coordinate)
    return None


def _cached(sheet, formula_sheet, row: int, col: int):
    raw = formula_sheet.cell(row, col).value
    cached = sheet.cell(row, col).value
    if isinstance(raw, str) and raw.startswith("=") and _blank(cached):
        return "uncached"
    if isinstance(cached, str) and cached.startswith("#"):
        return "error"
    return cached


def _legend(title: str) -> bool:
    text = str(title or "")
    return any(word in text for word in _LEGEND) or "legend" in text.casefold()


def _window_has_merge(sheet, max_row: int, max_col: int) -> bool:
    for rng in sheet.merged_cells.ranges:
        if rng.min_row <= max_row and rng.max_row >= 1 and rng.min_col <= max_col and rng.max_col >= 1:
            return True
    return False


def _table_has_merge(sheet, header_row: int, last_row: int, columns: tuple[int, ...]) -> bool:
    for rng in sheet.merged_cells.ranges:
        if rng.max_row < header_row or rng.min_row > last_row:
            continue
        if any(rng.min_col <= col <= rng.max_col for col in columns):
            return True
    return False


def _nonempty(sheet, max_row: int, max_col: int) -> bool:
    for row in range(1, max_row + 1):
        for col in range(1, max_col + 1):
            if not _blank(sheet.cell(row, col).value):
                return True
    return False


def _window(row: int, last_row: int, col: int, last_col: int) -> tuple[str, str]:
    return f"{get_column_letter(col)}{row}", f"{get_column_letter(last_col)}{max(last_row, row)}"


def _matches(value, aliases: tuple[str, ...]) -> bool:
    text = _norm(value)
    if not text:
        return False
    return any(text == _norm(alias) or text.startswith(_norm(alias)) for alias in aliases)


def _norm(value) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch not in " \t\r\n/／、，,;；:：()（）")


def _blank(value) -> bool:
    return value is None or not str(value).strip()


def _carrier(suffix: str) -> Literal["xlsx", "xlsm"]:
    return "xlsm" if suffix == ".xlsm" else "xlsx"

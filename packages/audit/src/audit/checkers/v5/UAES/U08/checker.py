"""UAES control-plan checks owned by this directory leaf.

Only clauses listed in resources.RESOURCE are executable. A carrier this leaf
does not read is UNABLE. It does not call another checker version.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource
from documents.excel import load_excel_workbook
from resources import RESOURCE

_HEADER_ROWS = 40
_HEADER_COLS = 30
_DATA_ROWS = 200
_UNSUPPORTED = "该预置 Checker 不支持此原件载体。"
_UNREADABLE = "无法读取控制计划工作簿，不能判定本条款。"


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
        self._unreadable = False
        self.checks = [_Clause(self, clause_id) for clause_id in sorted(RESOURCE["clauses"])]

    def close(self) -> None:
        book, self._book = self._book, None
        if book is not None:
            book.close()

    def evaluate(self, clause_id: str) -> AuditResultItem:
        suffix = Path(self.audit_file.file_url).suffix.lower()
        if suffix not in RESOURCE["supported_suffixes"]:
            return self._item(clause_id, "UNABLE", _UNSUPPORTED, checks_performed=0)
        book = self._open()
        if book is None:
            return self._item(clause_id, "UNABLE", _UNREADABLE, checks_performed=0)
        spec = RESOURCE["clauses"][clause_id]
        if "resource_headers" in spec:
            return self._resources(clause_id, book, suffix, spec)
        return self._marks(clause_id, book, suffix, spec)

    def _open(self):
        if self._book is None and not self._unreadable:
            try:
                self._book = load_excel_workbook(str(self.audit_file.file_url))
            except Exception:
                self._unreadable = True
        return self._book

    def _resources(self, clause_id: str, book, suffix: str, spec: dict) -> AuditResultItem:
        decisions = []
        for sheet in book.worksheets:
            found = _headers(sheet, {"operation": spec["operation_headers"], "resource": spec["resource_headers"]})
            if found is None or "resource" not in found[1]:
                continue
            row, columns = found
            gaps, filled = _paired(sheet, row, columns.get("operation"), columns["resource"])
            if not filled and not gaps:
                filled = _filled_below(sheet, row, columns["resource"])
            if gaps:
                decisions.append(("NG", "控制计划存在未填写生产资源的工序。", _evidence(suffix, sheet.title, gaps[:8])))
            elif filled:
                decisions.append(("PASS", "已读取各工序对应的生产资源。", _evidence(suffix, sheet.title, filled[:8])))
            else:
                decisions.append(("NG", "控制计划生产资源列没有内容。", _evidence(suffix, sheet.title, [sheet.cell(row, columns["resource"]).coordinate])))
        return self._choose(clause_id, decisions, "控制计划没有生产资源列。", book, suffix)

    def _marks(self, clause_id: str, book, suffix: str, spec: dict) -> AuditResultItem:
        decisions = []
        for sheet in book.worksheets:
            found = _headers(sheet, {"mark": spec["mark_headers"]})
            if found is None or "mark" not in found[1]:
                continue
            row, columns = found
            filled = _filled_below(sheet, row, columns["mark"])
            if filled:
                decisions.append(("PASS", "已读取特性类型或特殊特性列中的特性标记。", _evidence(suffix, sheet.title, filled[:8])))
            else:
                decisions.append(("NG", "特性类型或特殊特性列没有特性标记。", _evidence(suffix, sheet.title, [sheet.cell(row, columns["mark"]).coordinate])))
        return self._choose(clause_id, decisions, "控制计划没有特性类型或特殊特性列。", book, suffix)

    def _choose(self, clause_id: str, decisions: list, missing: str, book, suffix: str) -> AuditResultItem:
        # An incomplete sheet blocks PASS. A second sheet cannot wash that out.
        if any(item[0] == "NG" for item in decisions):
            return self._decided(clause_id, next(item for item in decisions if item[0] == "NG"))
        if any(item[0] == "PASS" for item in decisions):
            return self._decided(clause_id, next(item for item in decisions if item[0] == "PASS"))
        return self._item(clause_id, "NG", missing, evidence=_sheet_evidence(book, suffix))

    def _decided(self, clause_id: str, decision: tuple) -> AuditResultItem:
        status, suggestion, evidence = decision
        if status == "PASS" and not evidence.sources:
            return self._item(clause_id, "UNABLE", _UNREADABLE, checks_performed=1)
        return self._item(clause_id, status, suggestion, evidence=evidence)

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


def _headers(sheet, aliases: dict[str, tuple[str, ...]]):
    best = None
    for row in range(1, _HEADER_ROWS + 1):
        found: dict[str, int] = {}
        for col in range(1, _HEADER_COLS + 1):
            for role, names in aliases.items():
                if role not in found and _matches(sheet.cell(row, col).value, names):
                    found[role] = col
        if found and (best is None or len(found) > len(best[1])):
            best = (row, found)
            if len(found) == len(aliases):
                break
    return best


def _matches(value, aliases: tuple[str, ...]) -> bool:
    text = _norm(value)
    if not text:
        return False
    return any(text == _norm(alias) or text.startswith(_norm(alias)) for alias in aliases)


def _norm(value) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch not in " \t\r\n/／、，,;；:：()（）")


def _blank(value) -> bool:
    return value is None or not str(value).strip()


def _paired(sheet, row: int, operation_col: int | None, resource_col: int):
    if operation_col is None:
        return [], _filled_below(sheet, row, resource_col)
    gaps, filled = [], []
    for offset in range(1, _DATA_ROWS + 1):
        operation = sheet.cell(row + offset, operation_col)
        resource = sheet.cell(row + offset, resource_col)
        if _blank(operation.value):
            continue
        if _blank(resource.value):
            gaps.append(operation.coordinate)
        else:
            filled.append(resource.coordinate)
    return gaps, filled


def _filled_below(sheet, row: int, col: int) -> list[str]:
    found = []
    for offset in range(1, _DATA_ROWS + 1):
        cell = sheet.cell(row + offset, col)
        if not _blank(cell.value):
            found.append(cell.coordinate)
    return found


def _carrier(suffix: str) -> Literal["xlsx", "xlsm"]:
    return "xlsm" if suffix == ".xlsm" else "xlsx"


def _evidence(suffix: str, sheet: str, cells: list[str]) -> AuditEvidence:
    kind = _carrier(suffix)
    return AuditEvidence(
        required="structure",
        used="structure" if cells else "none",
        source_format=kind,
        prepared_format=kind,
        sources=tuple(EvidenceSource(kind="structure", locator=json.dumps({"kind": "xlsx_cell", "sheet": sheet, "cell": cell}, ensure_ascii=False)) for cell in cells),
    )


def _sheet_evidence(book, suffix: str) -> AuditEvidence:
    title = book.worksheets[0].title if book.worksheets else "Sheet"
    return _evidence(suffix, title, ["A1"] if book.worksheets else [])

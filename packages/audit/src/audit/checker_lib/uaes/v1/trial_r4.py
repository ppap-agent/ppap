"""R4 TA:1: compare the identifiers the original fields actually state."""

from __future__ import annotations

import re

from openpyxl.utils.cell import coordinate_to_tuple

from audit.checker_lib.uaes.native import NativeFacts, UnknownFact
from audit.checker_lib.uaes.v1.reader import HOLLOW, PLACEHOLDERS, compact, version_of
from audit.checker_lib.uaes.v1.trial import PART_LABELS, PSW, UaesTrialAgentR3, UaesTrialDocxAgentR3, kind_matches, name_of

# TA:1 allows width, spaces and hyphens only. Do not change case, underscore,
# slashes, or strip other punctuation (and do not NFKC-fold unrelated symbols).
_WIDTH = str.maketrans({c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)})
_PSW_NUMBER = ("客户零件号", "CustPartNumber", "Cust.PartNumber", "Customer Part Number")


def part_key(value):
    return re.sub(r"[\s\-‐‑]+", "", value.translate(_WIDTH))


def numbers(facts, aliases):
    """An inline labelled value or the cell directly right of its (possibly merged) label."""
    accepted = {compact(label).rstrip(".") for label in aliases}
    values = set()

    def split(text):
        label, _, value = str(text or "").replace("：", ":").partition(":")
        labels = {compact(t).rstrip(".") for t in re.split(r"[/／\n]+", label) if t.strip()}
        return bool(labels) and labels <= accepted, value.strip()

    def take(value):
        if not value or not part_key(value) or compact(value) in PLACEHOLDERS | HOLLOW or split(value)[0]:
            raise UnknownFact("有件号栏未能读到明确的件号")
        values.add(value)

    for table in facts.data.get("tables", ()):
        for address, cell in table["cells"].items():
            if cell.get("anchor", address) != address:
                continue
            matches, inline = split(cell.get("value"))
            if not matches:
                continue
            row, col = coordinate_to_tuple(address)
            facts.cell(table, row, col)
            if not inline:
                merge = next((m for m in table.get("merges", ()) if (m[0], m[1]) == (row, col)), None)
                col = (merge[3] if merge else col) + 1
                inline = str(facts.cell(table, row, col) or "").strip() if col <= table["cols"] else ""
            take(inline)
    for value, location in facts.observations():
        if location["kind"] not in {"docx_paragraph", "pdf_block"}:
            continue
        for line in value.splitlines():
            matches, inline = split(line)
            if matches and inline:
                facts.witness(location, line, facts.version)
                take(inline)
    return values


def compare(agent, reader):
    group = agent.group_context
    psws = [] if group is None else [item for kind, items in group.by_doc_type.items() if kind_matches(kind, PSW) for item in items if item.audit_id != agent.audit_file.audit_id]
    if not psws:
        return "UNABLE", "没有读到可比对的送审件号基准或同批 PSW 客户零件号"
    try:
        own = numbers(reader.native(), PART_LABELS)
        basis = set()
        for item in psws:
            stated = numbers(NativeFacts(item.file_url, version_of(item), reader.witness), _PSW_NUMBER)
            if not stated:
                raise UnknownFact(f"未能从《{name_of(item)}》读取客户零件号")
            basis.update(stated)
    except UnknownFact as exc:
        return "UNABLE", str(exc)
    a, b = {part_key(v) for v in own}, {part_key(v) for v in basis}
    detail = f"试装单件号“{'、'.join(sorted(own)) or '未读到'}”；同批PSW客户零件号“{'、'.join(sorted(basis))}”（{'、'.join(map(name_of, psws))}）"
    if len(a) != 1 or len(b) != 1:
        return "UNABLE", detail + "；尚不能确定唯一的对比对象"
    return ("PASS", detail + "；仅归一全半角、空格和连字符后逐字相同") if a == b else ("NG", detail + "；比对源错配")


class UaesTrialAgentR4(UaesTrialAgentR3):
    def part_number(self, lines):
        return self._from_legacy(lambda reader: compare(self, reader))


class UaesTrialDocxAgentR4(UaesTrialDocxAgentR3):
    def part_number(self, lines):
        return compare(self, self)

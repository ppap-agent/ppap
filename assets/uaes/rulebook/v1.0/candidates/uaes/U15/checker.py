"""UAES U15 candidate: trial assembly form, UAES v1.0 revision 3 (TA:1).

A leaf carries only checker.py and resources.py and may not import the UAES implementation. resources.py
declares the UAES trial form readers as carriers; at run time `Audit` extends the declared reader class with
the revision 3 clause method below, reusing the reader's area, flow and tick-mark reading. Verdicts are decided
in code; the image model only reads the UAES signatures off the original.
"""

from __future__ import annotations

import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, RuleContext
from resources import RESOURCE

FLOW = ("流程结束", "流程完成")
# Traditional spellings folded before matching ("流程結束").
TRADITIONAL = str.maketrans({"結": "结", "書": "书", "論": "论", "檢": "检", "驗": "验", "裝": "装", "過": "过", "記": "记", "錄": "录", "轉": "转"})
# Item 5: the UAES functions whose columns count; each found by its column label.
FUNCTIONS = ("工艺", "质量", "项目")
PART_LABELS = ("试装零件号", "零件号", "零件编号", "件号", "PartNo", "PartNumber")
PSW = ("零件提交保证书", "PSW")


def compact(value: Any) -> str:
    """Comparable text: width-folded, spaces removed, case-folded, trailing colon dropped (as the UAES reader)."""
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold().rstrip(":：")


def folded(text: str) -> str:
    """Item 2's normalisation: width, case, traditional spellings, and every separator dropped."""
    return re.sub(r"[\W_]+", "", compact(str(text).translate(TRADITIONAL)))


def part_key(value: str) -> str:
    """Item 6's normalisation: full/half width, spaces and hyphens; then compared character by character."""
    return re.sub(r"[\s\-‐‑‒–—―－_]+", "", unicodedata.normalize("NFKC", str(value))).upper()


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


def name_of(item: Any) -> str:
    return str((getattr(item, "file_metadata", None) or {}).get("original_filename") or Path(str(item.file_url)).name)


def file_lines(path: str) -> list[str]:
    """An original's text by lines; a spreadsheet label cell and the value right of it read as one "label：value" line."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        import fitz

        with fitz.open(path) as document:
            lines = "\n".join(page.get_text() for page in document).splitlines()
        # A PDF label line followed by its value line.
        return [*lines, *(f"{lines[index]}：{lines[index + 1]}" for index in range(len(lines) - 1))]
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        from audit.checker_lib.excel import load_workbook

        book = load_workbook(path)
        try:
            lines = []
            for sheet in book.worksheets:
                for row in sheet.iter_rows():
                    values = [str(cell.value).strip() for cell in row if cell.value is not None and str(cell.value).strip()]
                    lines.extend(values)
                    lines.extend(f"{values[index]}：{values[index + 1]}" for index in range(len(values) - 1))
            return lines
        finally:
            book.close()
    if suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "ignore")
        return [re.sub(r"<[^>]+>", "", paragraph) for paragraph in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)]
    return []


def labelled(lines: list[str], labels: tuple[str, ...]) -> list[str]:
    wanted = {compact(label) for label in labels}
    found = []
    for line in lines:
        match = re.match(r"\s*(.+?)\s*[:：]\s*(.+)", unicodedata.normalize("NFKC", line), re.S)
        if match and re.sub(r"[（(].*?[)）]", "", compact(match.group(1))) in wanted and match.group(2).strip():
            found.append(match.group(2).strip().splitlines()[0].strip())
    return list(dict.fromkeys(found))


def kind_matches(kind: Any, terms: tuple[str, ...]) -> bool:
    from audit.kinds import PpapDocumentType
    from audit.presets import customer_presets

    names = [str(kind)]
    resolved = PpapDocumentType.from_string(str(kind))
    if resolved != PpapDocumentType.UNKNOWN:
        names.append(resolved.get_type_name())
    names.extend(preset.title for preset in customer_presets() if preset.customer == "UAES" and preset.source_type == kind)
    return any(compact(term) in compact(name) for term in terms for name in names)


class TrialCore:
    """Items 2, 5 and 6 of TA:1, shared by the spreadsheet/PDF and Word readers."""

    READER: Any = None

    def flow(self, lines: list[str]) -> list[str]:
        text = folded("".join(lines))
        return [state for state in FLOW if folded(state) not in text]

    def functions(self, lines: list[str]) -> list[str]:
        """The UAES function columns the form sets: a label line naming the function (with 签字/签名/确认 or alone)."""
        found = []
        for name in FUNCTIONS:
            for line in lines:
                label = re.sub(r"^uaes", "", re.sub(r"[\W_a-z]+$", "", re.sub(r"[\W_]+", "", compact(line.split("：")[0]))))
                bare = re.sub(r"(签字|签名|确认|会签)$", "", label)
                # "工艺工程师(签字)", or "工艺签字"; a bare "项目" is a project field, not a sign-off column.
                if bare == name + "工程师" or (bare == name and bare != label):
                    found.append(name)
                    break
        return found

    def signatures(self, present: list[str], observe: Any) -> tuple[str, str]:
        if not present:
            return "MISSING", "UAES工艺、质量、项目工程师签字栏"
        fields = {name: f"UAES侧“{name}”职能栏有手写签名或电子签章；打印体姓名、空白、只写“/”或“N/A”为false；供应商方签字不算。" for name in present}
        values, _unreadable = observe(fields)
        signed = [name for name in present if values[name] is True]
        unsigned = [name for name in present if values[name] is False]
        needed = 3 if len(present) == 3 else 2
        if len(present) == 3 and unsigned:
            return "NG", "模板设工艺、质量、项目三栏，" + "、".join(unsigned) + "栏未签（按业务阶段未完成的仍按缺失判，请补签）"
        if len(present) < 3 and len(present) - len(unsigned) < needed:
            return "NG", f"UAES侧工艺、质量、项目工程师中签署的不足两方（模板设{'、'.join(present)}栏，" + (f"{'、'.join(unsigned)}未签" if unsigned else "栏位不足两方") + "）"
        if len(signed) < min(needed, len(present)) or any(values[name] is None for name in present):
            return "UNABLE", "UAES侧职能栏签字尚未能从原件全部核实"
        return "PASS", "UAES侧" + "、".join(signed) + "栏均已签署"

    def part_number(self, lines: list[str]) -> tuple[str, str]:
        own = labelled(lines, PART_LABELS)
        if not own:
            return "UNABLE", "试装单的试装零件号未能读出"
        group = self.group_context
        psws = [] if group is None else [item for kind, items in group.by_doc_type.items() if kind_matches(kind, PSW) for item in items if item.audit_id != self.audit_file.audit_id]
        if not psws:
            return "UNABLE", "同批次没有试装单以外的关联件号基准，也没有零件提交保证书（PSW）可取客户零件号，无法比对试装零件号"
        basis = [value for item in psws for value in labelled(file_lines(str(item.file_url)), ("客户零件号", "CustPartNumber", "Cust.PartNumber"))]
        if not basis:
            return "UNABLE", f"PSW（{'、'.join(map(name_of, psws))}）的客户零件号未能读出"
        source = f"取值来源：本次提交的PSW客户零件号（{'、'.join(map(name_of, psws))}）"
        if any(part_key(mine) == part_key(theirs) for mine in own for theirs in basis):
            return "PASS", f"试装零件号与PSW客户零件号一致（{source}）"
        return "NG", f"比对源错配：试装零件号“{'、'.join(own)}”与PSW客户零件号“{'、'.join(basis)}”不一致（{source}）"


class TrialClauses(TrialCore):
    """TA:1 over the declared UAES trial form reader (spreadsheet or PDF)."""

    def conclusions(self, ctx: RuleContext) -> tuple[str, str]:
        marks = self.READER._marks
        unknown, unmarked = [], []
        for label, positive, negative in self.READER.resources.TRIAL_CHOICES:
            written = self.choice(ctx, label)
            if written is None:
                unknown.append(f"{label}（未读到选项）")
                continue
            yes, no = marks(written, compact(positive), compact(negative))
            if no and not yes:
                return "NG", f"{label}勾选的是“{negative}”"
            if not yes and not no:
                unmarked.append(label)
            elif yes and no:
                unknown.append(f"{label}（读到“{written}”）")
        if unmarked:
            try:
                return self.absent("、".join(unmarked) + "的勾选")
            except GuardStop as stop:
                return stop.status, stop.message
        if unknown:
            return "UNABLE", "尚不能唯一确认" + "、".join(unknown) + "的实际勾选；文字标签不代表已选择"
        return "PASS", "试装结论勾选“通过”，检验结论勾选“合格”"

    @rule("试装单区域、流程、结论勾选、UAES签字与试装零件号")
    def ta_1(self, ctx: RuleContext):
        lines = file_lines(str(self.audit_file.file_url))
        results: list[tuple[str, str]] = []
        missing = [area for area in self.READER.resources.TRIAL_AREAS if not self.mentioned(ctx, area)]
        results.append(self.lacking("、".join(f"“{area}”" for area in missing) + "区域") if missing else ("PASS", "有流转记录、试装结论、检验结论区域"))
        absent = self.flow(lines)
        results.append(self.lacking("流转记录中的" + "、".join(f"“{state}”" for state in absent)) if absent else ("PASS", "流转记录有“流程结束”“流程完成”"))
        results.append(self.conclusions(ctx))
        try:
            status, message = self.signatures(self.functions(lines), lambda fields: self._from_legacy(lambda agent: agent.visual_observation(fields)))
        except GuardStop as stop:
            status, message = stop.status, stop.message
        results.append(self.lacking(message) if status == "MISSING" else (status, message))
        results.append(self.part_number(lines))
        return combine(results)

    def lacking(self, what: str) -> tuple[str, str]:
        try:
            return self.absent(what)
        except GuardStop as stop:
            return stop.status, stop.message


class TrialDocxClauses(TrialCore):
    """TA:1 over the declared UAES Word trial form reader."""

    @rule("试装单区域、流程、结论勾选、UAES签字与试装零件号")
    def ta_1(self):
        facts = self.native()
        resources, marks = self.READER.resources, self.READER._marks
        lines = file_lines(str(self.audit_file.file_url))
        results: list[tuple[str, str]] = []
        missing = [area for area in resources.TRIAL_AREAS if not facts.contains(area)]
        results.append(("NG", "原件中没有" + "、".join(missing) + "区域") if missing else ("PASS", "有三个区域"))
        absent = self.flow(lines)
        results.append(("NG", "流转记录中没有" + "、".join(absent)) if absent else ("PASS", "流转记录有“流程结束”“流程完成”"))
        picks = [marks(facts.field((label,)), positive, negative) for label, positive, negative in resources.TRIAL_CHOICES]
        if (False, True) in picks:
            results.append(("NG", "试装或检验结论选中了未通过/不合格"))
        elif (False, False) in picks and not facts.has_unread_content():
            results.append(("NG", "试装结论或检验结论未勾选"))
        elif (True, True) in picks or (False, False) in picks:
            results.append(("UNABLE", "尚不能唯一确认两个结论的实际勾选状态"))
        try:
            status, message = self.signatures(self.functions(lines), self.visual_observation)
        except Exception as exc:  # an unknown image fact (UnknownFact) leaves this item open
            status, message = "UNABLE", str(exc) or "UAES侧职能栏签字尚未能从原件核实"
        results.append(("NG", "原件中没有" + message) if status == "MISSING" else (status, message))
        results.append(self.part_number(lines))
        try:
            return combine(results)
        except GuardStop as stop:
            return stop.status, stop.message


def combine(results: list[tuple[str, str]]) -> tuple[str, str]:
    """Six items all hold: any NG fails, any open item leaves it to a person, else it passes."""
    failed = [message.rstrip("。") for status, message in results if status == "NG"]
    if failed:
        return "NG", "试装单" + "；".join(failed) + "。"
    open_ = [message.rstrip("。") for status, message in results if status != "PASS"]
    if any(status == "ERR" for status, _message in results):
        raise GuardStop(Decision.ERR, "试装单" + "；".join(open_) + "。")
    if open_:
        raise unable("试装单" + "；".join(open_) + "，需人工复核。")
    return "PASS", "试装单" + "；".join(message.rstrip("。") for _status, message in results) + "。"


class Audit(AgentAudit):
    """Runs the declared UAES reader extended with this leaf's clause method (see the module docstring)."""

    CLAUSES: dict[str, type] = {"UaesTrialAgent": TrialClauses, "UaesTrialDocxAgent": TrialDocxClauses}

    def _implementation(self) -> Any:
        if self._agent is None and self._failure is None:
            reader = super()._implementation()
            if reader is None:
                return None
            base = type(reader)
            close = getattr(reader, "close", None)
            if callable(close):
                close()
            try:
                extended = type(base.__name__ + "R3", (self.CLAUSES[base.__name__], base), {"READER": sys.modules[base.__module__]})
                self._agent = extended(self.audit_file, self._implementation_rule(), group_context=self.group_context)
            except BaseException as exc:
                self._agent, self._failure = None, exc
                raise
        return super()._implementation()


def create_audit(audit_file, rule, group_context=None):
    return Audit(audit_file, rule, group_context, resource=RESOURCE)

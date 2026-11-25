"""UAES U03: laboratory accreditation certificate, UAES v1.0 revision 3 (LAB:1).

The `…R3` readers add the clause method below to the UAES laboratory certificate readers, reusing their deadline
reading. Verdicts are decided in code; the image model only reads the issuing body's seal (and the laboratory name
when no label carries it).
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes import records
from audit.checker_lib.uaes.records import UaesLabQualificationAgent, UaesLabQualificationDocxAgent
from audit.engine.rule_engine import Decision, GuardStop, RuleContext

# Item 2: ISO/IEC 17025, and the six branches; "其他 ILAC 互认成员机构" is read as the ILAC MRA signatories' marks.
STANDARD = "17025"
BRANCHES = {
    "CNAS": (r"CNAS", r"中国合格评定国家认可委员会"),
    "DAkkS": (r"DAkkS", r"Deutsche\s*Akkreditierungsstelle"),
    "A2LA": (r"A2LA",),
    "UKAS": (r"UKAS",),
    "ILAC-MRA": (r"ILAC[\s\-_]*MRA",),
    "其他ILAC互认成员机构": (
        r"ILAC",
        # ILAC MRA signatories' accreditation bodies (acronyms as they mark certificates).
        r"ANAB", r"IAS", r"PJLA", r"NVLAP", r"SCC", r"CALA", r"EMA", r"COFRAC", r"ACCREDIA", r"ENAC", r"RvA", r"BELAC", r"SAS", r"Akkreditierung\s*Austria",
        r"SWEDAC", r"DANAK", r"FINAS", r"NA", r"PCA", r"CAI", r"SNAS", r"NAH", r"SA", r"HAA", r"IPAC", r"ESYD", r"INAB", r"TÜRKAK", r"NATA", r"IANZ", r"JAB", r"IAJapan",
        r"KOLAS", r"HKAS", r"TAF", r"SAC", r"DSM", r"NABL", r"KAN", r"BoA", r"PAO", r"SANAS", r"INMETRO", r"Cgcre", r"OAA", r"ema", r"ONAC", r"INACAL", r"IAAC", r"APAC",
    ),
}  # fmt: skip
# Acronyms read as whole words only; short ones too common inside other words ("NA", "SA", "EMA") are not read.
TOO_SHORT = {"NA", "SA", "ema", "EMA", "IAS", "SAS", "CAI", "SAC", "PCA", "NAH", "HAA"}
LAB_NAME = ("实验室名称", "被认可机构名称", "被认可机构", "机构名称", "Nameoflaboratory", "LaboratoryName", "Nameofconformityassessmentbody")
REPORT_DATE = r"(?:签发日期|报告日期|发布日期|批准日期|Dateofissue|Issuedate|Reportdate)[:：]?(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2})"


def compact(value: Any) -> str:
    """Comparable text: width-folded, spaces removed, case-folded, trailing colon dropped (as the UAES reader)."""
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold().rstrip(":：")


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


def branch(text: str) -> str | None:
    """The first accreditation branch the certificate's text writes."""
    for name, patterns in BRANCHES.items():
        for pattern in patterns:
            if pattern in TOO_SHORT:
                continue
            if re.search(rf"(?<![A-Za-z]){pattern}(?![A-Za-z])", text):
                return name
    return None


def parse_date(text: str) -> date | None:
    parts = re.split(r"[-/.年月]", text)
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return None


def file_text(path: str) -> str:
    """The plain text of a same-round original: a PDF's text layer, a spreadsheet's cells, a Word document's paragraphs."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        import fitz

        with fitz.open(path) as document:
            return "\n".join(page.get_text() for page in document)
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        from audit.checker_lib.excel import load_workbook

        book = load_workbook(path)
        try:
            return "\n".join(str(cell.value) for sheet in book.worksheets for row in sheet.iter_rows() for cell in row if cell.value is not None)
        finally:
            book.close()
    if suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            return re.sub(r"<[^>]+>", " ", archive.read("word/document.xml").decode("utf-8", "ignore"))
    return ""


def lab_name(text: str) -> str | None:
    """The accredited laboratory's name written after its label, if any."""
    for line in text.splitlines():
        match = re.match(r"\s*(.+?)\s*[:：]\s*(.+)", line)
        if match and compact(match.group(1)) in {compact(label) for label in LAB_NAME} and match.group(2).strip():
            return match.group(2).strip()
    return None


class LabCore:
    """LAB:1 decided over the certificate's text, its deadline and two image facts."""

    READER: Any = None
    audit_file: Any
    group_context: Any

    def certificate_text(self) -> str:
        return file_text(str(self.audit_file.file_url))

    def reports(self) -> list[Any]:
        """Same-round test or verification reports, by the names the UAES reader recognises."""
        if self.group_context is None:
            return []
        names = self.READER.resources.LAB_REPORT_NAMES
        own = self.audit_file.audit_id
        return [
            item
            for item in self.group_context.by_audit_id.values()
            if item.audit_id != own and any(term in str((item.file_metadata or {}).get("original_filename") or Path(str(item.file_url)).name) for term in names)
        ]

    STOP_ON_EXPIRY = False
    holder = staticmethod(lab_name)

    def report_names_holder(self, text, name):
        return compact(name) in compact(text)

    def accreditation(self, text, problems):
        flat = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
        if STANDARD not in flat:
            problems.append("证书中没有ISO/IEC 17025实验室认可字样")
        hit = branch(unicodedata.normalize("NFKC", text))
        if hit is None:
            problems.append("证书中没有CNAS、DAkkS、A2LA、UKAS、ILAC-MRA或其他ILAC互认成员机构的认可标识")
        return hit, {}

    def read_marks(self, values, unreadable, problems, open_):
        raise NotImplementedError

    def decide(self, text: str, deadline: date, observe: Any) -> tuple[str, str]:
        reminder = self.READER._lab_reminder(self.group_context)
        open_: list[Any]
        problems, open_ = [], []
        # 1. The deadline, against the audit date.
        if deadline <= date.today():
            problems.append(f"证书截止日期{deadline}不晚于当前日期，资质已到期")
            if self.STOP_ON_EXPIRY:
                return "NG", "实验室资质证书" + problems[0] + "。" + reminder
        hit, mark_fields = self.accreditation(text, problems)
        # 3. The issuing body's seal, and a readable laboratory name.
        name = self.holder(text)
        fields = {"issuer_seal": "证书上盖有发证机关（认可机构）的印章；只有文字“印章/盖章”或空白不算。"}
        if name is None:
            fields["lab_name"] = "证书上写有被认可实验室（机构）的名称，并且名称清晰可读。"
        fields.update(mark_fields)
        error = None
        try:
            values, unreadable = observe(fields)
        except GuardStop as stop:
            values, unreadable, error = dict.fromkeys(fields), set(), stop
        if mark_fields:
            hit = self.read_marks(values, unreadable, problems, open_)
        if values["issuer_seal"] is False:
            problems.append("证书上没有发证机关印章")
        elif values["issuer_seal"] is None:
            (problems if "issuer_seal" in unreadable else open_).append("发证机关印章无法辨识" if "issuer_seal" in unreadable else "发证机关印章尚未能从原件核实")
        if "lab_name" in fields and values.get("lab_name") is not True:
            readable = values.get("lab_name") is None and "lab_name" not in unreadable
            (open_ if readable else problems).append("被认可机构名称尚未能从原件核实" if readable else "被认可机构名称不可读")
        # The certificate covers the round's reports only under the same laboratory name and while valid.
        for item in self.reports():
            report = file_text(str(item.file_url))
            title = str((item.file_metadata or {}).get("original_filename") or Path(str(item.file_url)).name)
            if name is None:
                open_.append(f"同批报告{title}的出具实验室需与证书人工比对（证书上的实验室名称未能读出）")
            elif not self.report_names_holder(report, name):
                open_.append(f"同批报告{title}中未读到证书上的实验室名称“{name}”，是否为同一实验室（含子公司、事业部对应）需人工比对")
            issued = [day for found in re.findall(REPORT_DATE, re.sub(r"\s+", "", report), re.I) if (day := parse_date(found))]
            if issued and max(issued) > deadline:
                problems.append(f"同批报告{title}签发日期{max(issued)}晚于证书截止日期{deadline}，证书失效")
        basis = f"截止日期{deadline}；认可标识：{hit or '无'}"
        if problems:
            unreadable_note = "，请人工复核" if any("无法辨识" in item or "不可读" in item for item in problems) else ""
            return "NG", "实验室资质证书" + "；".join(problems) + unreadable_note + "。" + reminder
        if error is not None:
            raise GuardStop(error.decision, "实验室资质证书" + basis + "；印章与机构名称未能读取：" + error.message)
        if open_:
            raise unable("实验室资质证书" + basis + "；" + "；".join(open_) + "。" + reminder)
        return "PASS", f"实验室资质证书{basis}，为ISO/IEC 17025认可证书，有发证机关印章，被认可机构名称可读。" + reminder


class LabClauses(LabCore, UaesLabQualificationAgent):
    """LAB:1 over the declared UAES laboratory certificate reader (spreadsheet or PDF)."""

    @rule("实验室资质证书截止日期、认可标识与发证印章")
    def lab_1(self, ctx: RuleContext):
        if self.unread_pages():
            raise unable("证书原件是扫描页面（没有文字层），检查程序不读取扫描件，需人工复核。")
        deadlines = self.deadlines(ctx)
        if len(deadlines) > 1:
            raise unable(f"原件写有多个截止日期（{'、'.join(sorted(map(str, deadlines)))}），尚不能确定证书截止日期，需人工复核。")
        return self.decide(self.certificate_text(), next(iter(deadlines)), lambda fields: self._from_legacy(lambda agent: agent.visual_observation(fields)))


class LabDocxClauses(LabCore, UaesLabQualificationDocxAgent):
    """LAB:1 over the declared UAES Word laboratory certificate reader."""

    @rule("实验室资质证书截止日期、认可标识与发证印章")
    def lab_1(self):
        from audit.engine.checkers.date_utils import normalize_excel_date

        facts = self.native()
        deadline = normalize_excel_date(facts.field(self.READER.resources.LAB_DEADLINE))
        if deadline is None:
            return "UNABLE", "认可证书的截止日期未能可靠识别，需人工复核。"
        try:
            return self.decide(self.certificate_text(), deadline, self.visual_observation)
        except GuardStop as stop:
            return stop.status, stop.message


# ---- the readers the leaf names ----


class UaesLabQualificationAgentR3(LabClauses):
    READER = records


class UaesLabQualificationDocxAgentR3(LabDocxClauses):
    READER = records

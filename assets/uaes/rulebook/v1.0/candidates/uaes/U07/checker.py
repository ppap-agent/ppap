"""UAES U07 candidate: PFMEA against UAES v1.0 revision 3 (PFMEA:1–PFMEA:5).

The reader is the UAES PFMEA implementation that resources.py declares (its tables, failure items, same-round lists
and PGQP1); this leaf adds the revision 3 clause methods on top of it at run time. A leaf may not import a company
implementation, so the class and its module's names are taken from the carrier the shared loader built. Verdicts are
code over cell and text facts; a model only reads, through the implementation's reading stack.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from resources import RESOURCE

# Names of the UAES PFMEA reader's module this leaf uses; bound from the carrier's module when the reader is built.
_READER_NAMES = (
    "Place",
    "compact",
    "original_name",
    "unable",
    "_LISTS",
    "_PGQP1",
    "_SCORES",
    "Item",
    "UaesPfmeaAgent",
    "meaningful",
    "combine",
    "pgqp1",
    "special_list",
    "Decision",
    "GuardStop",
    "RuleContext",
    "Sheet",
    "Table",
)


def _norm(value: Any) -> str:
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold()


# ---- clause vocabulary (PFMEA:2) -------------------------------------------------------------------------------

# The placeholder words PFMEA:2 lists; "□" and a black redaction block read as unfilled.
PLACEHOLDERS = {
    _norm(word)
    for word in (
        "见附表",
        "见附件",
        "待定",
        "TBD",
        "NA",
        "N/A",
        "—",
        "-",
        "/",
        "同上",
        "见上",
        "略",
        "*",
        "待补",
        "后续提供",
        "XX",
        "XXXX",
        "123456",
        "#N/A",
        "#REF!",
        "#DIV/0!",
        "#VALUE!",
        "#NAME?",
        "#NUM!",
        "#NULL!",
        "□",
        "☐",
        "■",
        "█",
    )
}
# Step-6 cells PFMEA:2 counts as not substantive for a high-risk item: blank, "无", "N/A" (and the placeholders).
NOT_SUBSTANTIVE = PLACEHOLDERS | {_norm(word) for word in ("无", "None", "none|无", "None\n无", "没有")}
_DATE = re.compile(r"\d{2,4}\s*[./\-年]\s*\d{1,2}(?:\s*[./\-月]\s*\d{1,2})?|\d{1,2}\s*[./月]\s*\d{1,2}\s*日?|\bW\d{1,2}\b|第\d+周", re.I)
_POKA_YOKE = ("防错", "防呆", "poka", "全自动锁死", "自动锁死")
_MANUAL_INSTRUCTION = ("指导书", "作业指导", "workinstruction")
_SPOT_CHECK = "抽检"

# AIAG-VDA PFMEA action priority (AP) by S, O, D bands; used only where a table writes AP instead of RPN.
_O_BANDS, _D_BANDS = ((8, 10), (6, 7), (4, 5), (2, 3), (1, 1)), ((7, 10), (5, 6), (2, 4), (1, 1))
_AP = {
    (9, 10): ("HHHH", "HHHH", "HHHM", "HMLL", "LLLL"),
    (7, 8): ("HHHH", "HHHM", "HMMM", "MMLL", "LLLL"),
    (4, 6): ("HHMM", "MMML", "MLLL", "LLLL", "LLLL"),
    (2, 3): ("MMLL", "LLLL", "LLLL", "LLLL", "LLLL"),
    (1, 1): ("LLLL", "LLLL", "LLLL", "LLLL", "LLLL"),
}


def action_priority(s: int, o: int, d: int) -> str:
    row = next(value for (low, high), value in _AP.items() if low <= s <= high)
    band = next(index for index, (low, high) in enumerate(_O_BANDS) if low <= o <= high)
    column = next(index for index, (low, high) in enumerate(_D_BANDS) if low <= d <= high)
    return row[band][column]


def placeholder(value: Any) -> bool:
    return _norm(value) in PLACEHOLDERS


def substantive(value: Any) -> bool:
    text = _norm(value)
    return bool(text) and text not in NOT_SUBSTANTIVE and bool(re.search(r"[0-9a-z一-鿿]", text))


# ---- characteristic marks shared by PFMEA:3 and PFMEA:4 -------------------------------------------------------

_SAFETY = {"cc", "sc/1", "1", "s", "★", "安全", "关键"}
_MAJOR = {"sc", "sc/2", "2", "d", "f", "重要", "主要", "跑道框"}
# Symbols the clauses themselves name: the two equivalence levels, YC/YS, and the independent codes 3 and 5.
_KNOWN = _SAFETY | _MAJOR | {"yc", "ys", "3", "5"}
_TOKEN = re.compile(r"sc/[12]|安全|关键|重要|主要|跑道框|[a-z]+|\d+|[^\s,，、;；/]")


def mark_tokens(value: Any) -> list[str]:
    return _TOKEN.findall(_norm(value).replace("／", "/"))


def mark_class(value: Any) -> frozenset[str]:
    """The levels a mark states: safety/critical and major per the equivalence lists, any other code as itself."""
    out = set()
    for token in mark_tokens(value):
        out.add("安全/关键级" if token in _SAFETY else "重要/主要级" if token in _MAJOR else token)
    return frozenset(out)


def unknown_marks(value: Any) -> list[str]:
    return [token for token in mark_tokens(value) if token not in _KNOWN]


# Characteristic names: "71#平行度5", "79#-1螺栓孔距离3,5", "3#孔径  3,5（毛坯机加）".
_SERIAL = re.compile(r"^\s*\d+(?:\s*-\s*\d+)?\s*[#＃]\s*(?:-\s*\d+)?")
_CLASS_SUFFIX = re.compile(r"(?:[\s,，、]*(?:[1235]|cc|sc(?:/[12])?|yc|ys|★))+\s*(?=[（(]|$)", re.I)


def characteristic_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value)).split("\n")[0]
    text = _SERIAL.sub("", text)
    text = _CLASS_SUFFIX.sub("", text.strip())
    return _norm(text)


def tolerance(value: Any) -> str:
    text = _norm(value).replace("+/-", "±").replace("+-", "±")
    return text


# ---- PFD operations (PFMEA:5) ---------------------------------------------------------------------------------

_TRANSPORT_NAME = re.compile(r"搬运|运输|转运|移动|transport|move", re.I)
_STEP = re.compile(r"\d+(?:[.\-]\d+)*")


def _words(text: str) -> tuple[set[str], set[str]]:
    """CJK bigrams and Latin/number tokens of at least three characters."""
    folded = _norm(text)
    cjk = re.findall(r"[一-鿿]+", folded)
    bigrams = {run[index : index + 2] for run in cjk for index in range(len(run) - 1)}
    tokens = {token for token in re.findall(r"[a-z0-9]{3,}", folded)}
    return bigrams, tokens


def same_process(a: str, b: str) -> bool:
    bigrams_a, tokens_a = _words(a)
    bigrams_b, tokens_b = _words(b)
    if bigrams_a & bigrams_b:
        return True
    return any(token in _norm(b) for token in tokens_a) or any(token in _norm(a) for token in tokens_b)


class _Revision3:
    """Revision 3 clause methods over the UAES PFMEA reader."""

    # ---- raw cells --------------------------------------------------------------------------------------------

    @staticmethod
    def raw(sheet: Sheet, row: int, column: int) -> Any:
        merged = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, row, column) if sheet.ctx.ops is not None else None
        anchor = (merged.min_row, merged.min_col) if merged else (row, column)
        return sheet.ws.cell(*anchor).value

    @staticmethod
    def anchored_here(sheet: Sheet, row: int, column: int) -> bool:
        merged = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, row, column) if sheet.ctx.ops is not None else None
        return merged is None or merged.min_row == row

    def header_columns(self, sheet: Sheet) -> dict[str, int]:
        """Step-6 and risk columns found by their labels in the table header band (header row and the two rows below)."""
        section = self.section(sheet, "PFMEA失效区")
        header = section.resolved.header_row
        _rows, columns = self.extent(sheet)
        labels: dict[int, str] = {}
        for row in range(max(1, header - 1), header + 3):
            for column in range(1, columns + 1):
                value = sheet.ws.cell(row, column).value
                if value is not None and str(value).strip():
                    labels.setdefault(column, "")
                    labels[column] += " " + _norm(value)
        found: dict[str, int] = {}
        taken = next((column for column, text in sorted(labels.items()) if "采取的措施" in text or "actionstaken" in text), None)
        for column, text in sorted(labels.items()):
            after = taken is not None and column > taken
            if "rpn" in text.replace("\n", "") or "风险优先数" in text or re.search(r"r\s*p\s*n", text):
                found.setdefault("RPN'" if after else "RPN", column)
            if re.fullmatch(r"\s*ap\s*", text) or "行动优先级" in text or "actionpriority" in text:
                found.setdefault("AP'" if after else "AP", column)
            if "建议措施" in text or "recommend" in text:
                found.setdefault("建议措施", column)
            if "责任" in text or "responsib" in text:
                found.setdefault("责任人", column)
            if "目标完成日期" in text or "完成日期" in text or "targetcompletion" in text or "targetdate" in text:
                found.setdefault("目标日期", column)
            if after:
                if re.search(r"(^|\s)s($|\s)|严重度|sev", text):
                    found.setdefault("S'", column)
                elif re.search(r"(^|\s)o($|\s)|频度|occ", text):
                    found.setdefault("O'", column)
                elif re.search(r"(^|\s)d($|\s)|探测度|det", text):
                    found.setdefault("D'", column)
        if taken is not None:
            found["采取的措施"] = taken
        return found

    def step_column(self, sheet: Sheet) -> int | None:
        section = self.section(sheet, "PFMEA失效区")
        header = section.resolved.header_row
        _rows, columns = self.extent(sheet)
        for column in range(1, columns + 1):
            for row in range(max(1, header - 1), header + 3):
                text = _norm(sheet.ws.cell(row, column).value)
                if any(word in text for word in ("工序号", "过程编号", "processno", "工序编号", "op号", "步骤号")):
                    return column
        function = section.columns(optional=tuple(spec.canonical_name for spec in self._m.resources.DOCUMENT[2])).get("功能")
        if function is None or function.header_col < 2:
            return None
        left = function.header_col - 1
        rows, _ = self.extent(sheet)
        values = [str(sheet.ws.cell(row, left).value).strip() for row in range(header + 1, rows + 1) if sheet.ws.cell(row, left).value not in (None, "")]
        numeric = [value for value in values if _STEP.fullmatch(value)]
        return left if numeric and len(numeric) >= len(values) / 2 else None

    # ---- identity ---------------------------------------------------------------------------------------------

    def part_number(self, ctx: RuleContext) -> tuple[str | None, str]:
        """The labelled part number; else the one number the PFMEA header writes, when the other submitted originals of
        this round state exactly that number beside a part-number label (identity matched exactly, never guessed)."""
        part, why = super().part_number(ctx)
        if part is not None:
            return part, why
        written: set[str] = set()
        for sheet in self.sheets(ctx):
            top = self.section(sheet, "PFMEA失效区").resolved.header_row
            _rows, columns = self.extent(sheet)
            for cell in sheet.scan_non_empty(row_min=1, row_max=top, col_min=1, col_max=columns):
                written |= parts_in(cell.value())
        stated: set[str] = set()
        for item in self.group_context.by_audit_id.values() if self.group_context else ():
            if item.audit_id == self.audit_file.audit_id or (item.file_metadata or {}).get("role") == "reference":
                continue
            try:
                stated |= labelled_parts(str(item.file_url))
            except Exception:
                continue
        # The one number the header writes, confirmed letter for letter by another submitted original's labelled field.
        if len(written) == 1 and written <= stated:
            return next(iter(written)), ""
        return None, f"{why}；表头写有{'、'.join(sorted(written)) or '无'}，同批次其他提交文件标明的零件号为{'、'.join(sorted(stated)) or '无'}，两者不能唯一对上"

    # ---- PFMEA:1 ----------------------------------------------------------------------------------------------

    _GENERIC = ("参考以往经验", "以往经验", "参考经验", "历史经验", "首次发行", "无", "/", "n/a", "na", "见附件", "见附表")

    @staticmethod
    def lesson(text: str) -> str | None:
        cited = UaesPfmeaAgent.lesson(text)
        if cited is None:
            return None
        folded = _norm(text)
        for term in ("经验教训", "lessonslearned", "历史问题", "客户抱怨", "过往失效案例", "类似产品问题"):
            folded = folded.replace(term, "")
        for word in _Revision3._GENERIC:
            folded = folded.replace(_norm(word), "")
        return cited if len(re.sub(r"[\W_]", "", folded)) >= 4 else None

    @staticmethod
    def vague(text: str) -> bool:
        """A lessons-learned mention that only says "参考以往经验" or the like, naming no problem, batch or product."""
        folded = _norm(text)
        if not any(term in folded for term in ("经验教训", "lessonslearned", "以往经验", "历史问题", "客户抱怨", "过往失效", "类似产品")):
            return False
        return _Revision3.lesson(text) is None and any(_norm(word) in folded for word in ("参考以往经验", "以往经验", "参考经验", "历史经验", "类似产品"))

    def revision_records(self, ctx: RuleContext) -> tuple[list[str], list[str]]:
        """(valid records, incomplete records) of every change-history block of every sheet, read over the whole sheet."""
        valid, partial = [], []
        # Specific labels first: "改版理由和内容 Revision reason" is the content, not the version.
        groups = {
            "内容": ("修改说明", "修改内容", "变更内容", "更改内容", "改版理由", "修订说明", "revisionexplain", "revisionreason", "修改记录", "变更说明"),
            "修改人": ("修改人", "修订人", "变更人", "revisedby", "更改人", "作成", "madeby"),
            "日期": ("日期", "date"),
            "版本": ("版本号", "版本", "版次", "rev.no", "revno", "rev.", "ver"),
        }
        for sheet in (Sheet(ctx, ws) for ws in ctx.workbook.worksheets):
            rows, columns = self.extent(sheet)
            for row in range(1, rows + 1):
                found: dict[str, int] = {}
                for column in range(1, columns + 1):
                    text = _norm(sheet.ws.cell(row, column).value)
                    if not text or len(text) > 40:
                        continue
                    for name, words in groups.items():
                        if name not in found and any(_norm(word) in text for word in words) and column not in found.values():
                            found[name] = column
                            break
                if len(found) < 4:
                    continue
                # Records follow the label row, or stand above it where a footer block writes its labels last.
                below = list(range(row + 1, min(rows, row + 30) + 1))
                above = list(range(row - 1, max(0, row - 30), -1))
                lines = []
                for direction in (below, above):
                    for line in direction:
                        if not any(meaningful(self.raw(sheet, line, column)) for column in found.values()):
                            break
                        lines.append(line)
                for line in lines:
                    values = {name: self.raw(sheet, line, column) for name, column in found.items()}
                    shown = "；".join(f"{name}“{' '.join(str(value).split())}”" for name, value in values.items() if meaningful(value))
                    where = f"“{sheet.title}”第{line}行"
                    if all(meaningful(value) and not placeholder(value) for value in values.values()):
                        for column in found.values():
                            self.cite(sheet, line, column)
                        valid.append(f"{where}（{shown}）")
                    else:
                        partial.append(f"{where}（{shown}）")
        return valid, partial

    @rule("经验教训引用与修改记录")
    def rule_1(self, ctx: RuleContext):
        problems: list[str] = []
        try:
            status, basis = super().rule_1(ctx)
        except GuardStop as stop:
            if stop.decision != Decision.UNABLE:
                raise
            status, basis = "UNABLE", stop.message
        if status == "NG":
            vague = self.vague_place(ctx)
            if vague:
                status, basis = "UNABLE", f"{vague}只写了笼统的经验表述，无法确定所指的具体问题、批次或产品，按无法辨识处理，不按“已引用”放行，需人工复核。"
            else:
                problems.append("表头、附录、变更履历及修订说明中没有经验教训的具体引用（历史问题、客户抱怨、过往失效案例或类似产品问题）")
        valid, partial = self.revision_records(ctx)
        if not valid:
            if str(self.audit_file.file_url).lower().endswith(".pdf") and (reason := self.unreadable()):
                raise unable(f"{reason}，修改记录尚未读取，需人工复核。")
            detail = f"（已检索全表，读到不完整的记录：{'；'.join(partial[:3])}）" if partial else "（已检索全部工作表的全部行）"
            problems.append("没有一条同时写明版本号、修改日期、修改内容描述与修改人的修改记录" + detail)
        if problems:
            return "NG", "；".join(problems) + "。"
        if status == "UNABLE":
            raise unable(f"修改记录有效（{valid[0]}）；但{basis}")
        return "PASS", f"{basis}；修改记录有效：{valid[0]}。"

    def vague_place(self, ctx: RuleContext) -> str:
        for sheet in (Sheet(ctx, ws) for ws in ctx.workbook.worksheets):
            rows, columns = self.extent(sheet)
            for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns):
                if self.vague(str(cell.value())):
                    self.cite(sheet, cell.row_index, cell.col_index)
                    return f"“{sheet.title}”第{cell.row_index}行"
        return ""

    # ---- PFMEA:2 ----------------------------------------------------------------------------------------------

    @staticmethod
    def integer(value: Any) -> int | None:
        try:
            number = Decimal(str(value).strip())
        except Exception:
            return None
        if not number.is_finite() or number != number.to_integral_value():
            return None
        return int(number)

    @rule("S/O/D评分合理性七项")
    def rule_2(self, ctx: RuleContext):
        items = self.items(ctx)
        found: dict[str, list[str]] = {}
        gaps: list[str] = []

        def fail(kind: str, text: str) -> None:
            found.setdefault(kind, []).append(text)

        columns_by_sheet: dict[str, dict[str, int]] = {}
        by_condition: dict[tuple[str, ...], tuple[Item, tuple]] = {}
        for item in items:
            ctx.record_execution()
            sheet = item.sheet
            extra = columns_by_sheet.setdefault(sheet.title, self.header_columns(sheet))
            where = item.where()
            # 6. Each failure-cause row writes its own initial S, O and D; blank and placeholder are separate problems.
            cause_column = item.columns.get("失效起因")
            cause_row = cause_column is not None and self.anchored_here(sheet, item.row, cause_column) and meaningful(self.raw(sheet, item.row, cause_column))
            scores: dict[str, int] = {}
            for name, letter in _SCORES.items():
                column = item.columns[name]
                raw = self.raw(sheet, item.row, column)
                value = item.values.get(name, "")
                if cause_row and (raw is None or not str(raw).strip()):
                    if self.uncomputed(sheet, item.row, column):
                        gaps.append(f"{where}{letter}是没有计算结果的公式")
                        continue
                    self.at(item, name)
                    fail("6", f"{where}初始{letter}为空白")
                    continue
                if cause_row and placeholder(raw):
                    self.at(item, name)
                    fail("6", f"{where}初始{letter}填占位词“{str(raw).strip()}”")
                    continue
                number = self.integer(value) if value else None
                if value and (number is None or not 1 <= number <= 10):
                    if str(self.audit_file.file_url).lower().endswith(".pdf") and re.fullmatch(r"\d+(\s+\d+)+", value):
                        gaps.append(f"{where}{letter}读出“{' '.join(value.split())}”（PDF转换把相邻行的评分合在一格）")
                        continue
                    self.at(item, name)
                    fail("1", f"{where}{letter}为“{value}”，不是1～10的整数")
                    continue
                if number is not None:
                    scores[name] = number
            if len(scores) < 3:
                continue
            s, o, d = scores["严重度"], scores["频度"], scores["探测度"]
            effect = item.values.get("失效后果", "")
            prevention, detection = item.values.get("预防控制", ""), item.values.get("探测控制", "")
            p, q = _norm(prevention), _norm(detection)
            # 2. Severity against the effect.
            if any(term in effect for term in ("安全", "法规")) and s < 9:
                self.at(item, "严重度")
                fail("2", f"{where}失效后果“{' '.join(effect.split())[:40]}”涉及安全/法规，S={s}低于9")
            if "轻微" in effect and "外观" in effect and not 2 <= s <= 4:
                self.at(item, "严重度")
                fail("2", f"{where}失效后果仅为轻微外观，S={s}不在2～4")
            # 3. Occurrence against the prevention control: the clause's hard lines only.
            no_prevention = not p or placeholder(prevention) or p in {"无", "none", "无预防控制", "无预防措施"}
            poka = any(word in p for word in _POKA_YOKE)
            # "仅人工指导书": every measure written is a work instruction ("测量作业指导书"); a second measure is not "仅".
            measures = [part for part in re.split(r"[、，,;；/和及]|\d+[、.)）]|\n", unicodedata.normalize("NFKC", prevention)) if _norm(part)]
            manual = bool(measures) and not poka and all(any(word in _norm(part) for word in _MANUAL_INSTRUCTION) for part in measures)
            if (no_prevention or manual) and o <= 3:
                self.at(item, "频度")
                fail("3", f"{where}预防控制{'为空/无' if no_prevention else '仅为人工指导书“' + ' '.join(prevention.split())[:30] + '”'}，O={o}≤3")
            if poka and o >= 8:
                self.at(item, "频度")
                fail("3", f"{where}预防控制有物理防错“{' '.join(prevention.split())[:30]}”，O={o}≥8")
            if s >= 9 and o == 1 and not poka:
                self.at(item, "频度")
                fail("3", f"{where}S={s}≥9，预防控制无物理防错或全自动锁死措施，O却为1")
            # 4. Detection against the detection control: the three hard lines.
            no_detection = not q or placeholder(detection) or q in {"无", "none", "无法探测", "无探测措施", "无探测控制"}
            if no_detection and d < 10:
                self.at(item, "探测度")
                fail("4", f"{where}无探测措施，D={d}<10")
            if _SPOT_CHECK in q and "自动" not in q and d <= 3:
                self.at(item, "探测度")
                fail("4", f"{where}探测为人工抽检“{' '.join(detection.split())[:30]}”，D={d}≤3")
            if any(word in q for word in _POKA_YOKE) and d >= 8:
                self.at(item, "探测度")
                fail("4", f"{where}探测为自动防错“{' '.join(detection.split())[:30]}”，D={d}≥8")
            # 5. Same failure mode and same controls, same scores.
            condition = tuple(_norm(item.values.get(name, "")) for name in ("失效模式", "失效起因", "预防控制", "探测控制"))
            if condition in by_condition and by_condition[condition][1] != (s, o, d):
                first, earlier = by_condition[condition]
                fail("5", f"{first.where()}与{where}失效模式、起因及控制相同，S/O/D却为{'/'.join(map(str, earlier))}与{s}/{o}/{d}")
            by_condition.setdefault(condition, (item, (s, o, d)))
            # 7. RPN = S×O×D, or AP by the action-priority table.
            risk = None
            if "RPN" in extra:
                raw = self.raw(sheet, item.row, extra["RPN"])
                rpn = self.integer(raw) if meaningful(raw) else None
                if rpn is None:
                    if self.uncomputed(sheet, item.row, extra["RPN"]):
                        gaps.append(f"{where}RPN是没有计算结果的公式")
                    elif cause_row:
                        self.cite(sheet, item.row, extra["RPN"])
                        fail("7", f"{where}RPN为“{'' if raw is None else str(raw).strip()}”，不是S×O×D={s * o * d}")
                elif rpn != s * o * d:
                    self.cite(sheet, item.row, extra["RPN"])
                    fail("7", f"{where}RPN={rpn}，不等于S×O×D={s}×{o}×{d}={s * o * d}")
                risk = "H" if s * o * d > 100 else "M" if s * o * d > 50 else "L"
            elif "AP" in extra:
                raw = _norm(self.raw(sheet, item.row, extra["AP"])).upper()
                expected = action_priority(s, o, d)
                if raw[:1] not in {"H", "M", "L"} or raw[:1] != expected:
                    self.cite(sheet, item.row, extra["AP"])
                    fail("7", f"{where}AP为“{raw}”，按S/O/D={s}/{o}/{d}查表应为{expected}")
                risk = expected
            # High-risk items close step 6: recommended action, owner and target date.
            if s >= 9 or risk == "H":
                why = f"S={s}" if s >= 9 else ("RPN=" + str(s * o * d) if "RPN" in extra else "AP=H")
                missing = []
                action = self.raw(sheet, item.row, extra["建议措施"]) if "建议措施" in extra else None
                if not substantive(action):
                    missing.append("建议措施")
                owner_column, date_column = extra.get("责任人"), extra.get("目标日期")
                owner = self.raw(sheet, item.row, owner_column) if owner_column else None
                if owner_column is not None and owner_column == date_column:
                    text = "" if owner is None else str(owner)
                    if not substantive(text) or not re.search(r"[一-鿿a-zA-Z]", _DATE.sub("", text)):
                        missing.append("责任人")
                    if not _DATE.search(text):
                        missing.append("目标完成日期")
                else:
                    if not substantive(owner):
                        missing.append("责任人")
                    date = self.raw(sheet, item.row, date_column) if date_column else None
                    if not substantive(date):
                        missing.append("目标完成日期")
                if missing:
                    for name in ("建议措施", "责任人", "目标日期"):
                        if name in extra:
                            self.cite(sheet, item.row, extra[name])
                    fail("高风险", f"{where}{why}属高风险，{'、'.join(missing)}没有实质内容")
            # Step 6 actions taken: complete revised scores, no higher risk, S' below S only on a design change.
            taken = self.raw(sheet, item.row, extra["采取的措施"]) if "采取的措施" in extra else None
            if substantive(taken):
                revised = {}
                for name in ("S'", "O'", "D'"):
                    value = self.raw(sheet, item.row, extra[name]) if name in extra else None
                    number = self.integer(value) if meaningful(value) else None
                    if number is None or not 1 <= number <= 10:
                        self.cite(sheet, item.row, extra.get(name, extra["采取的措施"]))
                        fail("措施后", f"{where}已填采取的措施，改进后{name}为“{'' if value is None else str(value).strip()}”，不是1～10的整数")
                    else:
                        revised[name] = number
                if len(revised) == 3:
                    s2, o2, d2 = revised["S'"], revised["O'"], revised["D'"]
                    if "AP" in extra and "RPN" not in extra:
                        order = {"L": 0, "M": 1, "H": 2}
                        if order[action_priority(s2, o2, d2)] > order[action_priority(s, o, d)]:
                            fail("措施后", f"{where}改进后AP={action_priority(s2, o2, d2)}高于初始AP={action_priority(s, o, d)}")
                    elif s2 * o2 * d2 > s * o * d:
                        fail("措施后", f"{where}改进后RPN={s2 * o2 * d2}高于初始RPN={s * o * d}")
                    if s2 < s and "设计" not in _norm(taken) and "design" not in _norm(taken):
                        fail("措施后", f"{where}改进后S′={s2}低于S={s}，采取的措施“{' '.join(str(taken).split())[:30]}”没有设计变更依据")
        if found:
            labels = {"1": "第1项", "2": "第2项", "3": "第3项", "4": "第4项", "5": "第5项", "6": "第6项", "7": "第7项", "高风险": "高风险项闭环", "措施后": "措施后评分"}
            parts = [f"{labels[kind]}：{'；'.join(texts[:3])}{f'等{len(texts)}处' if len(texts) > 3 else ''}" for kind, texts in sorted(found.items(), key=lambda pair: list(labels).index(pair[0]))]
            return "NG", "已核对" + str(len(items)) + "个失效项，不满足：" + "。".join(parts) + "。"
        if gaps:
            raise unable("、".join(gaps[:3]) + "，这些行的评分尚未读取，需人工复核。")
        basis = "RPN均等于S×O×D" if "RPN" in next(iter(columns_by_sheet.values()), {}) else "AP均与查表一致"
        return "PASS", (
            f"已核对{len(items)}个失效项：S/O/D均为1～10的整数且无空白或占位；未触发严重度与后果、O与预防控制、D与探测控制的倒挂线"
            f"（描述与评分无矛盾）；相同失效模式与控制条件评分一致；{basis}；高风险项与已采取措施的行均闭环。"
        )

    # ---- PFMEA:3 ----------------------------------------------------------------------------------------------

    def pfmea_characteristics(self, items: list[Item]) -> list[tuple[Item, set[str], set[str]]]:
        """(item, names, tolerances): the names a row gives in 功能/要求 and the specifications it writes."""
        out = []
        for item in items:
            names = {characteristic_name(item.values.get(key, "")) for key in ("功能", "要求")} - {""}
            tolerances = {tolerance(item.values.get(key, "")) for key in ("失效模式", "要求", "功能")} - {""}
            out.append((item, names, tolerances))
        return out

    def compare(self, label: str, rows: list[tuple[str, str, str, str, Any]], items: list[Item], *, causes: bool = False) -> tuple[list[str], list[str], list[str]]:
        """(failures, notes, pending) of comparing marked counterpart characteristics with the PFMEA rows."""
        table = self.pfmea_characteristics(items)
        failures, notes, pending = [], [], []
        for where, name, value, mark, cells in rows:
            # A change-history line under the list ("图纸更新 2024/8/27") is not a characteristic.
            if mark is not None and (re.fullmatch(r"\d{2,4}[/.\-]\d{1,2}[/.\-]\d{1,2}", _norm(mark)) or re.search(r"改版理由|revision|版本号|日期|date", _norm(name) + _norm(mark))):
                continue
            key_name, key_tolerance = characteristic_name(name), tolerance(value)
            if not key_name and not key_tolerance:
                pending.append(f"{where}特性（{mark}）的名称与规格未能从原件文字读取")
                continue
            paired = [item for item, names, tolerances in table if key_name in names and (not key_tolerance or key_tolerance in tolerances)]
            if not paired:
                if causes and key_name:
                    paired = [item for item in items if key_name in characteristic_name(item.values.get("失效起因", "")) or key_name in _norm(item.values.get("失效模式", ""))]
            if not paired:
                if cells:
                    self.cite(*cells)
                failures.append(f"{where}特性“{name} {value}”（{mark}）按“公差值＋特性名”在PFMEA中配对不上，缺项")
                continue
            if mark is None:
                continue
            theirs = mark_class(mark)
            odd = unknown_marks(mark)
            matches = [item for item in paired if mark_class(item.values.get("类别", "")) == theirs]
            if matches:
                if any(_norm(item.values.get("类别", "")) != _norm(mark) for item in matches):
                    notes.append(f"{where}“{mark}”与PFMEA第{matches[0].row}行“{matches[0].values.get('类别')}”写法不同，按同级/分隔符等价判一致")
                continue
            odd += [token for item in paired for token in unknown_marks(item.values.get("类别", ""))]
            if odd:
                pending.append(f"{where}特性“{name}”标识涉及规则与图例均未登记的符号“{'、'.join(sorted(set(odd)))}”，待供应商说明")
                continue
            for item in paired[:2]:
                self.at(item, "类别")
            shown = "、".join(f"第{item.row}行“{item.values.get('类别') or '空'}”" for item in paired[:3])
            failures.append(f"{where}特性“{name} {value}”标识为“{mark}”，PFMEA配对行{shown}，标识不一致")
        return failures, notes, pending

    @rule("特殊特性标识与清单覆盖")
    def rule_3(self, ctx: RuleContext):
        items = self.items(ctx)
        if not any("类别" in item.values for item in items):
            return "NG", "PFMEA没有“特殊特性”或“分类”列，关键/重要/特殊特性没有符号标识（第1项）。"
        marked = [item for item in items if mark_tokens(item.values.get("类别", ""))]
        if not marked:
            if reason := self.unreadable():
                raise unable(f"{reason}，可读部分的PFMEA特殊特性/分类列为空，需人工复核。")
            return "NG", "PFMEA“特殊特性/分类”列全部为空，没有任何行标注关键/重要/特殊特性符号（第1项）。"
        self.require_readable(_LISTS)
        unknown = self.untyped()
        if not any(self.batch_files(kind) for kind in _LISTS):
            if unknown:
                raise unable(f"同批次没有已识别的特殊特性清单，另有{len(unknown)}份原件类型未识别，尚不能确认是否缺少特殊特性清单，需人工复核。")
            self.need("特殊特性清单")
            return "NG", "同批次没有提交特殊特性清单（第2项）。"
        part, unread = self.part_number(ctx)
        if part is None:
            raise unable(f"{unread}，无法确定本文件物料编号，尚不能配对同物料编号的特殊特性清单，需人工复核。")
        project = self.project(self.sheets(ctx))
        characteristics, undetermined, unstated = self.batch_lists(ctx, part, project)
        if not characteristics and undetermined:
            # The project cannot be compared: the same-part characteristics are still compared, the project stays open.
            self.project = lambda sheets: "*"  # type: ignore[method-assign]
            try:
                characteristics, _same, _none = self.batch_lists(ctx, part, "*")
            finally:
                del self.project
        if not characteristics:
            if unknown:
                raise unable(f"同批次特殊特性清单中没有零件号{part}的特性，另有{len(unknown)}份原件类型未识别，需人工复核。")
            return "NG", f"同批次特殊特性清单中没有同项目、零件号{part}的特殊特性，未提交同项目、同物料编号的特殊特性清单（第2项）。"
        rows = []
        for label, row, name, value, mark, cells in characteristics:
            rows.append((f"{label}第{row}行", name, value, mark, cells.get("name", cells["mark"])))
        # Item 1: a characteristic the list marks must not be left unmarked in the PFMEA ("已知关键特性不得漏标").
        failures, notes, pending = self.compare("特殊特性清单", rows, items, causes=True)
        if failures:
            return "NG", f"按零件号{part}配对清单{len(rows)}项特性：" + "；".join(failures[:5]) + (f"等{len(failures)}处" if len(failures) > 5 else "") + "（第1、3、4项）。"
        if pending or undetermined or unknown:
            extra = (
                pending + ([f"{'、'.join(undetermined)}未写明项目（或与PFMEA项目不能比较），“同项目”尚不能确认"] if undetermined else []) + ([f"另有{len(unknown)}份原件类型未识别"] if unknown else [])
            )
            raise unable("已配对的特性名称、公差与标识一致，但" + "；".join(extra[:3]) + "，需人工复核。")
        note = f"（{'；'.join(notes[:3])}）" if notes else ""
        return "PASS", f"PFMEA有特殊特性符号列；同批次特殊特性清单中零件号{part}的{len(rows)}项特性均按“公差值＋特性名”在PFMEA中配对，名称、公差与符号一致{note}。"

    # ---- PFMEA:4 ----------------------------------------------------------------------------------------------

    @rule("PGQP1重要特性覆盖与一致")
    def rule_4(self, ctx: RuleContext):
        self.require_readable((_PGQP1,))
        plans = self.batch_files(_PGQP1)
        if not plans:
            if unknown := self.untyped():
                raise unable(f"同批次没有已识别的采购件质量计划，另有{len(unknown)}份原件类型未识别，尚不能确认是否缺少PGQP1，需人工复核。")
            self.need("采购件质量计划（PGQP1）")
            return "NG", "同批次没有提交采购件质量计划（PGQP1），整份未交，不以特殊特性清单或图纸替代比对，判不通过。"
        part, unread = self.part_number(ctx)
        if part is None:
            raise unable(f"{unread}，无法确定本文件零件号，尚不能配对同零件号的采购件质量计划，需人工复核。")
        others, rows, has_table = [], [], False
        for plan in plans:
            with self.reading(ctx, plan, pgqp1):
                for sheet in Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                    has_table = True
                    numbers = {compact(value).upper() for value in self.labelled(sheet, ("Part No/零件号",)) if compact(value)}
                    if part not in numbers:
                        others.extend(sorted(numbers) or ["未写零件号"])
                        continue
                    section = self.section(sheet, "重要特性区")
                    for row in section.rows("特性项目"):
                        name = row.value("特性项目")
                        if not meaningful(name):
                            continue
                        written = str(row.value("特殊特性") or "").strip()
                        # The 776 form writes the specification under "SC/特殊特性"; a symbol column holds marks only.
                        is_mark = bool(written) and all(token in _KNOWN for token in mark_tokens(written))
                        value = "" if is_mark else written
                        mark = written if is_mark else None
                        where = f"PGQP1“{sheet.title}”第{row.row_index}行"
                        rows.append((where, str(name).strip(), value, mark, (sheet, row.row_index, row.columns["特性项目"].header_col)))
        if not has_table:
            self.need("采购件质量计划（PGQP1）")
            return "NG", "同批次采购件质量计划中没有PGQP1表，判不通过。"
        if not rows:
            if others:
                self.need("采购件质量计划（PGQP1）", part=part, found=others[0])
                return "NG", f"同批次PGQP1的零件号为{'、'.join(sorted(set(others)))}，与PFMEA零件号{part}不同，没有同零件号的采购件质量计划，判不通过。"
            return "NG", f"同批次零件号{part}的PGQP1表中没有重要特性行，判不通过。"
        items = self.items(ctx)
        failures, notes, pending = self.compare("PGQP1", rows, items, causes=True)
        if failures:
            return "NG", f"PGQP1零件号{part}共{len(rows)}项重要特性：" + "；".join(failures[:5]) + (f"等{len(failures)}处" if len(failures) > 5 else "") + "。"
        if pending:
            raise unable("PGQP1重要特性均已覆盖，但" + "；".join(pending[:3]) + "，需人工复核。")
        unmarked = sum(1 for row in rows if row[3] is None)
        note = f"其中{unmarked}项PGQP1未写特殊特性符号，只核对名称与公差；" if unmarked else ""
        return "PASS", f"PGQP1零件号{part}全部{len(rows)}项重要特性均按“公差值＋特性名”在PFMEA中出现；{note}{'；'.join(notes[:2])}"

    # ---- PFMEA:5 ----------------------------------------------------------------------------------------------

    def pfd_operations(self, ctx: RuleContext) -> tuple[list[tuple[str, str, bool, str]], list[str]]:
        """(step, description, transport only, file) of every same-round PFD operation, and the PFDs not read.

        A flow chart table has a step column and an operation description column with the activity symbol lanes
        between them; a lane headed 搬运/运输 holds the transport activity."""
        operations, unread = [], []
        for item in self.batch_files("过程流程图"):
            name = original_name(item)
            with self.reading(ctx, item, self._flow):
                sheets = Table(ctx).find_sheets("过程流程图", on_missing=Decision.IGNORE)
                if len(sheets) != 1:
                    unread.append(f"{name}（未能确定唯一的工序表）")
                    continue
                sheet = sheets[0]
                try:
                    section = sheet.section("流程区")
                except GuardStop:
                    unread.append(f"{name}（未能定位工序号与过程描述表头）")
                    continue
                header = section.resolved.header_row
                step_column, text_column = section.column("工序号").header_col, section.column("过程描述").header_col
                lanes = {column: _norm(self.raw(sheet, header, column)) for column in range(step_column + 1, text_column)}
                for row in section.rows("工序号", "过程描述"):
                    step = "" if self.raw(sheet, row.row_index, step_column) is None else str(self.raw(sheet, row.row_index, step_column)).strip()
                    if step and not re.fullmatch(r"[\d.\s/、,，-]+", step):
                        break
                    description = " ".join(str(row.value("过程描述") or "").split())
                    if not description or not self.anchored_here(sheet, row.row_index, text_column):
                        continue
                    used = [column for column in lanes if any(char in _FLOW_SYMBOLS for char in str(self.raw(sheet, row.row_index, column) or ""))]
                    only_moves = bool(used) and all(_TRANSPORT_NAME.search(lanes[column]) for column in used)
                    if not used and _TRANSPORT_NAME.search(description) and not re.search(r"加工|检|装配|清洗", description):
                        only_moves = True
                    operations.append((step, description, only_moves, name))
        return operations, unread

    def pdf_steps(self) -> dict[str, list[str]]:
        """Step number -> the text of its function cell, read from the text layer of each PDF page."""
        import fitz

        out: dict[str, list[str]] = {}
        with fitz.open(str(self.audit_file.file_url)) as document:
            for page in document:
                blocks = page.get_text("blocks")
                head = [block for block in blocks if re.search(r"程编号|工序号|process\s*no", block[4], re.I)]
                if not head:
                    continue
                left = min(block[0] for block in head) - 5
                modes = [block[0] for block in blocks if "潜在失效模式" in block[4] or "potential failure mode" in block[4].casefold()]
                right = min(modes, default=left + 150)
                top = max(block[3] for block in head)
                starts = sorted((block[1], match.group(1)) for block in blocks if left <= block[0] < left + 20 and block[1] > top and (match := re.match(r"\s*(\d+(?:[.\-]\d+)*)", block[4])))
                for index, (y, step) in enumerate(starts):
                    end = starts[index + 1][0] if index + 1 < len(starts) else page.rect.y1
                    text = " ".join(" ".join(block[4].split()) for block in blocks if block[0] >= left and block[2] <= right + 2 and y - 2 <= block[1] < end)
                    out.setdefault(step, []).append(text)
        return out

    @rule("PFD主工序在PFMEA中覆盖")
    def rule_5(self, ctx: RuleContext):
        pfds = self.batch_files("过程流程图")
        if not pfds:
            if unknown := self.untyped():
                raise unable(f"同批次没有已识别的过程流程图，另有{len(unknown)}份原件类型未识别，尚不能确认是否提交了PFD，需人工复核。")
            return "NA", "同批次未提交过程流程图（PFD），本条不适用（依据：条款“同批次未提交过程流程图时，本条记不适用”）；不记作通过，缺件只在包级计一次。"
        operations, unread = self.pfd_operations(ctx)
        if unread and not operations:
            raise unable(f"过程流程图{'、'.join(unread)}的工序未能读取，需人工复核。")
        items = self.items(ctx)
        steps: dict[str, list[str]] = {}
        for sheet in {id(item.sheet.ws): item.sheet for item in items}.values():
            column = self.step_column(sheet)
            if column is None:
                continue
            current = None
            for item in [entry for entry in items if entry.sheet.ws is sheet.ws]:
                raw = self.raw(sheet, item.row, column)
                text = "" if raw is None else str(raw).strip()
                if _STEP.fullmatch(text):
                    current = text
                if current is not None:
                    steps.setdefault(current, []).append(" ".join(str(item.values.get(key, "")) for key in ("功能", "要求", "失效模式")))
        if str(self.audit_file.file_url).lower().endswith(".pdf"):
            # A PDF cell may lose its step number to the neighbouring text ("4\n电镀"): the page's own step column is read too.
            for step, texts in self.pdf_steps().items():
                steps.setdefault(step, []).extend(texts)
        if not steps:
            return "NG", "PFMEA没有可读的工序号列，缺少PFMEA与PFD的工序对应关系，判不通过。"
        missing, unsure, covered, moves = [], [], 0, []
        for written, description, only_moves, name in operations:
            # One PFD cell may number several operations ("7 8 9"): each number is an operation of its own.
            numbers = _STEP.findall(written) or [written]
            if only_moves:
                moves.append(f"{'/'.join(numbers)} {description}")
                continue
            for step in numbers:
                mine = [texts for key, texts in steps.items() if key == step or re.fullmatch(re.escape(step) + r"[.\-]\d+", key)]
                if not mine:
                    missing.append(f"PFD“{name}”工序{step}“{description}”")
                    continue
                if any(same_process(description, text) for texts in mine for text in texts):
                    covered += 1
                else:
                    unsure.append(f"工序{step}（PFD“{description}”，PFMEA“{' '.join(mine[0][0].split())[:30]}”）")
        reminder = f"提醒：PFD中只负责转运的工序{('、'.join(moves[:5]))}不要求失效分析，请留意其在PFMEA中是否体现。" if moves else ""
        if missing:
            return "NG", f"PFD主工序在PFMEA中没有对应工序号的失效分析：{'、'.join(missing[:6])}{f'等{len(missing)}道' if len(missing) > 6 else ''}。{reminder}"
        if unsure or unread:
            pending = ([f"工序号对上但名称无共同字词，核心工艺语义需人工确认：{'；'.join(unsure[:5])}"] if unsure else []) + ([f"另有过程流程图{'、'.join(unread)}未能读取"] if unread else [])
            raise unable("；".join(pending) + f"。其余{covered}道主工序已覆盖。{reminder}")
        return "PASS", f"PFD全部{covered}道主工序均在PFMEA中有相同工序号且工艺名称相符的失效分析。{reminder}"


_PART = re.compile(r"(?<![0-9A-Z])F[0-9A-Z]{9}(?![0-9A-Z])")
_PART_LABELS = ("零件号", "零件编号", "物料编号", "partnumber", "partno", "产品件号", "件号")


def parts_in(value: Any) -> set[str]:
    return set(_PART.findall(unicodedata.normalize("NFKC", "" if value is None else str(value)).upper().replace(" ", "")))


def labelled_parts(path: str) -> set[str]:
    """Part numbers another original writes beside a part-number label: cells of a workbook, text of a PDF."""
    from pathlib import Path

    found: set[str] = set()
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        import fitz

        with fitz.open(path) as document:
            text = unicodedata.normalize("NFKC", "".join(page.get_text() for page in document[:2]))
        for match in re.finditer(r"(零件号|零件编号|物料编号|Part\s*No\.?|Part\s*Number|产品件号)", text, re.I):
            found |= parts_in(text[match.end() : match.end() + 40])
        return found
    if suffix not in {".xlsx", ".xlsm", ".xls"}:
        return found
    from audit.checker_lib.excel import load_workbook_auto

    book = load_workbook_auto(path)
    for sheet in book.worksheets:
        for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 40), max_col=min(sheet.max_column, 40)):
            for index, cell in enumerate(row):
                text = _norm(cell.value)
                if not text or not any(label in text for label in _PART_LABELS):
                    continue
                values = parts_in(cell.value)
                if not values:
                    for neighbour in row[index + 1 : index + 9]:
                        if neighbour.value not in (None, ""):
                            values = parts_in(neighbour.value)
                            break
                found |= values
    return found


_FLOW_SYMBOLS = "◇○△□▽"
_BUILT: dict[type, type] = {}


def _flow_document(models: Any) -> Any:
    """The process flow chart table as the UAES flow chart resource declares it (step and description columns)."""
    ColumnModel, SectionModel, SheetModel = models.ColumnModel, models.SectionModel, models.SheetModel
    rows, columns = models.WHOLE_SHEET_ROWS, models.WHOLE_SHEET_COLS
    document = (
        (SheetModel(canonical_name="过程流程图", include_keywords=("过程流程图",)),),
        (
            SectionModel(
                canonical_name="流程区",
                sheet_name="过程流程图",
                header_anchors=("工序号", "过程描述"),
                header_hit=2,
                header_compact_match=True,
                header_search_max_row=rows,
                header_search_max_col=columns,
                row_anchors=("过程描述",),
                row_stop_on_first_blank=False,
                row_max_blank_streak=rows,
                row_stop_markers=("注释Notes", "prepared 编制"),
            ),
        ),
        (
            ColumnModel(canonical_name="工序号", section_name="流程区", aliases=("STP工序号", "步骤\nStep")),
            ColumnModel(canonical_name="过程描述", section_name="流程区", aliases=("OPERATION DESCRIPTION过程描述", "过程描述\nProcess description")),
        ),
    )
    return SimpleNamespace(DOCUMENT=document, PDF_SPECS=(models.STITCHED, models.PER_PAGE))


def _extend(base: type) -> type:
    """The UAES PFMEA reader `base` with the revision 3 clause methods and the flow chart table it reads for PFMEA:5."""
    if base not in _BUILT:
        module = sys.modules[base.__module__]
        globals().update({name: getattr(module, name) for name in _READER_NAMES})
        flow = _flow_document(module.resources)
        resource = module.combine(module.resources.DOCUMENT, module.special_list.DOCUMENT, module.pgqp1.DOCUMENT, flow.DOCUMENT)
        _BUILT[base] = type("UaesPfmeaRevision3", (_Revision3, base), {"_m": module, "_flow": flow, "EXTRA_RESOURCE": resource, "__module__": __name__})
    return _BUILT[base]


class Revision3Audit(AgentAudit):
    def _implementation(self):
        if self._failure is not None:
            raise self._failure
        agent = super()._implementation()
        if agent is None or isinstance(agent, _Revision3):
            return agent
        try:
            self._agent = _extend(type(agent))(self.audit_file, self._implementation_rule(), group_context=self.group_context)
        except BaseException as exc:
            self._failure = exc
            raise
        return self._agent


def create_audit(audit_file, rule, group_context=None):
    return Revision3Audit(audit_file, rule, group_context, resource=RESOURCE)

# ruff: noqa: E402, F821  # a candidate leaf binds the declared reader's names at run time; it may not import it

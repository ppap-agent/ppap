"""UAES U01 decision methods for signed v1.0 revision 4."""

from __future__ import annotations

import hashlib
import re
from io import BytesIO
from pathlib import Path
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.v1.inspection import (
    InspectionR3,
    Report,
    bare,
    limits,
)
from audit.checker_lib.uaes.v1.reader import Place, compact, original_name, unable, version_of
from audit.engine.rule_engine import RuleContext, Sheet

PICTURE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["handwritten_signature", "ink_seal", "issuer", "seal_type"],
    "properties": {
        "handwritten_signature": {"type": ["boolean", "null"]},
        "ink_seal": {"type": ["boolean", "null"]},
        "issuer": {"type": ["string", "null"]},
        "seal_type": {"enum": ["company", "inspection", "other", "unclear", None]},
    },
}


PICTURE_PROMPT = (
    "这是样品检验报告工作表“供应商负责人”签署区中嵌入的一张图片。请只描述图片本身，不判断是否合格。\n"
    "handwritten_signature：图片中有手写笔迹的签名为 true；只有印刷体文字、印章、标志或空白为 false；看不清为 null。\n"
    "ink_seal：图片中有公司印章（公章、专用章等的印迹或其图像）为 true；品牌标志、表头 logo、签名、文字为 false；看不清为 null。"
    "issuer照录章面的公司全称，seal_type区分仅有公司名称的公章(company)、质量检验专用章(inspection)、其他专用章(other)。无法辨识则填null；不得判断效力。"
)


class InspectionR4(InspectionR3):
    """Reader for signed UAES v1.0 revision 4."""

    def release(self, item: Any) -> tuple[str, str, str, bool] | None:
        """Read the *subject* cell of a native, single-page C Sample Release Report, never a name or a remark.
        Other layouts, multiple subjects and scans cannot exclude coverage."""
        import fitz

        path = Path(str(item.file_url))
        if path.suffix.lower() != ".pdf":
            return None
        metadata = getattr(item, "file_metadata", None) or {}
        expected = metadata.get("source_sha256") or metadata.get("sha256")
        if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise OSError("本轮冻结关联资料已变化，不能完成本项核对。")
        with fitz.open(str(path)) as document:
            if len(document) != 1 or self.unread_pages(str(path)):
                return None
            page = document[0]
            if "csamplereleasereport" not in compact(page.get_text()):
                return None
            matches, supplier, remark, approved, uaes = [], "", "", False, False
            for table in page.find_tables().tables:
                rows = table.extract()
                if not rows or not any("csamplereleasereport" in compact(value) for value in rows[0]):
                    continue
                for index, row in enumerate(rows):
                    for col, label in enumerate(row[:-1]):
                        if compact(label) in {"联合汽车电子有限公司", "联合汽车电子有限公司unitedautomotiveelectronicsystemsco.,ltd"}:
                            uaes = True
                        if compact(label) == "公司名称name":
                            supplier = (row[col + 1] or "").strip()
                            if supplier and (box := table.rows[index].cells[col + 1]):
                                self.witness({"kind": "pdf_region", "page": 1, "bbox": list(box)}, supplier, version_of(item))
                        if "uaesconditionofapproval/remark" in compact(label):
                            # Separate the printed footer from the customer remark, not arbitrary document words.
                            comment = re.split(r"UAES\s+Condition\s+of\s+approval\s*/\s*remark", label, flags=re.I)[-1]
                            remark = re.split(r"组长\s*[:：]|Team Leader", comment, flags=re.I)[0].strip()
                            remark = re.sub(r"\s*\d{4}[./-]\d{1,2}[./-]\d{1,2}\s*$", "", remark).strip()
                            box = table.rows[index].cells[col]
                            if box and remark:
                                self.witness({"kind": "pdf_region", "page": 1, "bbox": list(box)}, remark, version_of(item))
                        if re.search(r"批准\s*[:：]\s*[^\s:：]+\s+\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", label or ""):
                            approved = True
                            if box := table.rows[index].cells[col]:
                                self.witness({"kind": "pdf_region", "page": 1, "bbox": list(box)}, label, version_of(item))
                        if compact(label) != "零件号partno.":
                            continue
                        part = (row[col + 1] or "").strip()
                        box = table.rows[index].cells[col + 1]
                        if not box or not re.fullmatch(r"F[0-9A-Z]{9,}", part):
                            return None
                        matches.append((part, box))
            if len(matches) != 1:
                return None
            part, box = matches[0]
            # Don't exclude a document that also names another potential subject.
            if set(re.findall(r"(?<![A-Za-z0-9])F[0-9A-Z]{9,}(?![A-Za-z0-9])", page.get_text())) != {part}:
                return None
            self.witness({"kind": "pdf_region", "page": 1, "bbox": list(box)}, part, version_of(item))
            return part, supplier, remark, approved and uaes

    def supplier(self, report: Report) -> str | None:
        names = set()
        for cell in self.cells(report.sheet, row_max=report.header):
            match = re.fullmatch(r"\s*(?:Supplier\s*/\s*供应商|供应商(?:名称)?|Supplier)\s*[:：]\s*(.+?)\s*", self.raw(cell), re.I)
            if match:
                names.add(match[1])
                self.cite(report.sheet, cell.row_index, cell.col_index)
        return names.pop() if len(names) == 1 else None

    def inline_review(self, report: Report, row: int) -> bool:
        # A named engineer on the offending row is not an explicit acceptance or denial (#r4.1).
        for header in self.cells(report.sheet, row_max=report.header + 1):
            if not re.search(r"顾客工程师|客户工程师|customer\s+engineer", self.raw(header), re.I):
                continue
            col = header.col_index
            if report.sheet.cell(row, col).has_data():
                self.cite(report.sheet, row, col)
                return True
            pictures = self.pictures(report)
            if pictures is None or any(top <= row <= bottom and left <= col <= right for (top, left, bottom, right), _ in pictures):
                return True
        return False

    @rule("尺寸报告实测值列数与超差")
    def rule_1(self, ctx: RuleContext):
        self.readable()
        reports = self.reports(ctx, "尺寸报告")
        if not reports:
            if self.untabled():
                raise unable("PDF原件未能读出表格，尚不能确认是否包含尺寸报告，需人工复核。")
            return "NG", "缺少尺寸报告。"
        notes, wrong, outside, unknown, skipped = [], [], [], [], 0
        exceedances: list[tuple[str, str, bool]] = []
        for report in reports:
            filled = [column for column in report.band if any(report.sheet.cell(row.row_index, column).has_data() for row in report.rows)]
            where = f"尺寸报告“{report.sheet.title}”"
            if not filled or len(filled) % 3:
                self.cite(report.sheet, report.header, report.band[0])
                wrong.append(f"{where}实测值有读数的列数为{len(filled)}，不是3或3的倍数")
                continue
            notes.append(f"{where}实测值有读数的列数为{len(filled)}")
            for row in self.requirement_rows(report):
                over, open_, _values = self.ranges(report, row)
                requirement = row.value(f"{report.title}要求")
                if limits(requirement) is None and bare(requirement):
                    skipped += 1
                if over:
                    self.cite(report.sheet, row.row_index, row.columns[f"{report.title}要求"].header_col)
                    exceedances.append((str(row.value(f"{report.title}序号")), str(requirement), self.inline_review(report, row.row_index)))
                    outside.append(
                        Place.of(f"“{report.sheet.title}”第{row.row_index}行要求“{requirement}”实测值{'、'.join(value for value, _column in over)}超差", report.sheet, row.row_index, over[0][1])
                    )
                unknown += open_
        if wrong:
            return "NG", "；".join(wrong) + "。"
        if outside:
            listed = "；".join(map(self.named, outside[:8])) + ("等" if len(outside) > 8 else "")
            approvals = self.approvals()
            if not approvals and not any(signed for _, _, signed in exceedances):
                self.need("顾客工程批准或偏差许可")
                return "NG", f"{listed}；同批次没有顾客工程批准或偏差许可，按超差判不通过。"
            part, _ = self.identity(ctx)
            for ws in ctx.workbook.worksheets:
                if "cover" not in compact(ws.title) and "封面" not in ws.title:
                    continue
                for cell in self.cells(Sheet(ctx, ws), row_max=40):
                    match = re.fullmatch(r"\s*零件号\s*/\s*Subject Number\s*[:：]\s*(.+?)\s*", str(cell.value() or ""), re.I)
                    if match and match[1].strip() and match[1].strip() != part:
                        part = ""
            suppliers = {self.supplier(report) for report in reports}
            releases = [(item, self.release(item)) for item in approvals]
            details = []
            states = []
            for index, requirement, inline in exceedances:
                covered, uncertain = False, inline
                for item, release in releases:
                    if not release or not part or part == "未写" or "、" in part:
                        uncertain = True
                        continue
                    other, supplier, remark, approved = release
                    summary = f"《{original_name(item)}》第1页零件号{other}"
                    if other != part:
                        details.append(summary + "，不同零件")
                        continue
                    if len(suppliers) == 1 and None not in suppliers and supplier and supplier not in suppliers:
                        details.append(summary + "，供应商不同")
                        continue
                    # A clear global statement cannot waive an item. Other/qualified text stays unresolved.
                    broad = re.fullmatch(r"(?:经检验[,，]?)?(?:产品的?)?(?:尺寸(?:[、,，](?:材料|性能|试装))*均?合格[,，]?)?(?:满足使用要求[,，]?)?(?:产品)?(?:完全认可)?[。.]?", compact(remark))
                    if broad and re.search("合格|完全认可", remark):
                        details.append(summary + "，仅笼统认可、未指向超差项")
                        continue
                    # Explicit, unqualified acceptance in the customer's own approval box.
                    # Restrict positive coverage to the written item AND its requirement on this supplier's part.
                    acceptance = re.fullmatch(r"(?:同意|接受)第([^项]+)项尺寸偏差[，,]要求[:：]([^，,；;]+)[，,](?:本次实测超差予以接受|同意本次实测值)[。.]*", remark.strip())
                    if acceptance and approved and suppliers == {supplier} and supplier:
                        if acceptance[1].strip() == index:
                            if compact(acceptance[2]) == compact(requirement):
                                covered = True
                            else:
                                uncertain = True  # The item is named but the specification differs.
                        continue
                    uncertain = True
                states.append("covered" if covered and (not uncertain or (inline and len(releases) == 1)) else "unknown" if uncertain else "uncovered")
            suffix = "；".join(dict.fromkeys(details))
            if "uncovered" in states:
                self.need("覆盖该超差项的顾客工程批准或偏差许可", part=part or None)
                return "NG", f"{listed}；检验报告零件号{part}；{suffix}；至少一项超差无对应批准。"
            if "unknown" in states:
                raise unable(f"{listed}；{suffix}；尚不能确定每项是否获顾客明确接受（未明用途的逐项署名不作裁决），需人工复核。")
            notes.append("本批超差项均有相同零件和供应商、逐项明确接受的顾客批准")
        if unknown:
            raise unable("；".join(notes) + "；检查程序未能确认：" + "；".join(map(self.named, unknown[:8])) + ("等" if len(unknown) > 8 else "") + "，需人工复核是否超差。")
        note = f"（{skipped}项要求只写名义值、报告上没有公差，未按区间核对）" if skipped else ""
        return "PASS", "；".join(notes) + f"；未发现未经批准的超差{note}。"

    def picture(self, data: bytes | None) -> dict[str, Any]:
        cache = self.__dict__.setdefault("_picture_facts", {})
        key = hashlib.sha256(data or b"").hexdigest()
        if key not in cache:
            if data is None:
                cache[key] = {"handwritten_signature": None, "ink_seal": None}
            else:
                from PIL import Image

                output = BytesIO()
                with Image.open(BytesIO(data)) as image:
                    image.convert("RGB").save(output, format="PNG")
                cache[key] = self.transcribe(output.getvalue(), PICTURE_PROMPT, PICTURE_SCHEMA, "样品检验报告签署区的图片")
        return cache[key]

    @rule("有要求的报告供应商负责人签字与印章")
    def rule_3(self, ctx: RuleContext):
        self.readable()
        reports, missing = self.all_reports(ctx)
        if missing:
            return "NG", "文件缺少" + "、".join(missing) + "。"
        applicable = [report for report in reports if self.requirement_rows(report) or self.exemption(report) is not None]
        if not applicable:
            return "NA", "四份报告均在，要求列都没有内容，本条没有须签字的报告（要求列本身按IR:2判）。"
        unsigned, unknown, signed, seals, open_seal = [], [], [], [], False
        from openpyxl.utils.cell import get_column_letter

        for report in applicable:
            area = self.signature_area(report)
            if area is None:
                unsigned.append(f"“{report.sheet.title}”没有供应商负责人签署区")
                continue
            first, left, last, right = area
            self.cite(report.sheet, first, left)
            pictures = self.pictures(report)
            if pictures is None:
                unknown.append(f"“{report.sheet.title}”签署区的图片")
                continue
            inside = [(anchor, data) for anchor, data in pictures if first <= anchor[0] <= last and left <= anchor[1] <= right]
            if not inside:
                near = any(top <= last and bottom >= first and col <= right and end >= left for (top, col, bottom, end), _ in pictures)
                if near:
                    unknown.append(f"“{report.sheet.title}”供应商签署区与邻栏图片重叠，无法确定签字归属")
                else:
                    unsigned.append(f"“{report.sheet.title}”供应商负责人处没有签字")
                continue
            facts = [(anchor, self.picture(data)) for anchor, data in inside]
            for anchor, fact in facts:
                locator = {"kind": "xlsx_range", "sheet": report.sheet.title, "start": f"{get_column_letter(anchor[1])}{anchor[0]}", "end": f"{get_column_letter(anchor[3])}{anchor[2]}"}
                if fact.get("handwritten_signature") or fact.get("ink_seal"):
                    self.witness(locator, "签署区的签名" if fact.get("handwritten_signature") else "签署区的印章", visual=True)
            marks = [fact.get("handwritten_signature") for _anchor, fact in facts]
            if True in marks:
                signed.append(report.sheet.title)
            elif None in marks:
                unknown.append(f"“{report.sheet.title}”供应商负责人处图片是否为手写签名")
            else:
                unsigned.append(f"“{report.sheet.title}”供应商负责人处没有手写签字")
            supplier = self.supplier(report)
            stamps = []
            for anchor, fact in facts:
                stamp = fact.get("ink_seal")
                if stamp is True:
                    issuer, kind = fact.get("issuer"), fact.get("seal_type")
                    if not issuer or not supplier or anchor[3] > right or anchor[2] > last + 1:
                        stamp = None  # illegible issuer or ambiguous overlap with another area
                    elif issuer.strip() != supplier:
                        stamp = False
                    elif kind != "company":
                        stamp = None  # especially a quality-inspection-only seal: pending IR:3#r4.1
                    self.witness(
                        {"kind": "xlsx_range", "sheet": report.sheet.title, "start": f"{get_column_letter(anchor[1])}{anchor[0]}", "end": f"{get_column_letter(anchor[3])}{anchor[2]}"},
                        f"章面归属：{issuer or '不清'}；章类：{kind or '不清'}",
                        visual=True,
                    )
                stamps.append(stamp)
            if True in stamps:
                seals.append(report.sheet.title)
            elif None in stamps:
                open_seal = True
        if unsigned:
            return "NG", "；".join(unsigned) + "。"
        if unknown:
            raise unable("检查程序未能核实" + "、".join(unknown) + "，需人工复核。")
        if not seals:
            if open_seal:
                raise unable("供应商负责人均已签字；签署区图片是否为供应商印章未能核实，需人工复核。")
            return "NG", "要求的供应商负责人均已签字（" + "、".join(signed) + "），但签署区没有任何有效印章，签署不完整。"
        return "PASS", "有要求的报告（" + "、".join(signed) + "）供应商负责人均已签字，签署区有供应商印章（" + "、".join(seals) + "）。"

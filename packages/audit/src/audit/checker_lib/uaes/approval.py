"""UAES primary cover decision, dates and conditional deviation reconciliation."""

import re
from datetime import date

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.checkers.date_utils import normalize_excel_date

from .native import UaesSourceAgent, UnknownFact, key

_DATE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")


class UaesApprovalAgent(UaesSourceAgent):
    ROLES = ("组长", "审核", "批准")
    # The English halves of the bilingual role and date labels on the cover.
    LABEL_WORDS = {"日期", "date", "team", "leader", "checked", "approved", "by"}

    def cover_signatures(self):
        """Role -> (typed name, date) read beside each cover role label; None when only an image could tell."""
        import fitz

        facts = self.native()
        if facts.format != ".pdf":
            return None
        with fitz.open(facts.path) as document:
            page = document[0]
            words = [(fitz.Rect(word[:4]), word[4]) for word in page.get_text("words")]
            images = [fitz.Rect(image["bbox"]) for image in page.get_image_info()]
        labels = {}
        for role in self.ROLES:
            found = [rect for rect, text in words if text.rstrip(":：") == role and text[-1:] in ":："]
            if len(found) != 1:
                return None
            labels[role] = found[0]
        signed = {}
        for role, label in labels.items():
            # A role's entry is on its label line, or just under it, up to the next label on that line.
            bottom = label.y1 + label.height
            right = min((other.x0 for other in labels.values() if other.x0 > label.x0 and other.y0 < bottom and other.y1 > label.y0), default=float("inf"))
            entry = [(rect, text) for rect, text in words if rect.x0 > label.x1 - 1 and rect.x1 <= right + 1 and rect.y0 < bottom and rect.y1 > label.y0]
            day = next((normalize_excel_date(text) for _rect, text in entry if _DATE.fullmatch(text)), None)
            name = " ".join(text for _rect, text in entry if not _DATE.fullmatch(text) and text.rstrip(":：").casefold() not in self.LABEL_WORDS)
            if (not name or day is None) and any(image.intersects(fitz.Rect(label.x0, label.y0, min(right, 1e6), bottom)) for image in images):
                return None  # A handwritten signature image may fill the entry.
            for index, block in enumerate(next((page["blocks"] for page in facts.data.get("pages", []) if page["page"] == 1), []), 1):
                if any(fitz.Rect(block["bbox"]).contains(rect) for rect, _text in entry):
                    self.witness({"kind": "pdf_block", "page": 1, "block": index}, block["text"])
            signed[role] = (name, day)
        return signed

    @rule("UAES封面首要备注与实际否决签批及偏差期限")
    def rule_1(self):
        cover = self.native()
        # A table titled 封面/cover, otherwise the first page, which is what a cover is.
        titled = cover.tables(("封面", "cover"))
        page = titled[0] if len(titled) == 1 else cover.data["tables"][0] if not titled and cover.data["tables"] else None
        if page is None:
            raise UnknownFact("尚不能确定唯一的封面页。")
        rows = cover.rows({"item": ("项目", "检验项目", "试验项目", "报告内容"), "reject": ("否决", "Reject")}, table=page, anchor=("item",))
        seen, drawn = set(), False
        for row in rows:
            name = str(row["item"])
            for category in ("尺寸", "材料", "性能", "试装"):
                if category in name:
                    seen.add(category)
                    try:
                        mark = row["reject"]
                    except UnknownFact:
                        drawn = True  # A box drawn over by a graphic mark is read from the original image.
                        continue
                    if re.search(r"[×xX☑✓√✔]", str(mark or "")):
                        return "NG", "封面否决列有实际标记：" + name
        if len(seen) != 4:
            raise UnknownFact("尚不能完整识别尺寸、材料、性能、试装的否决列状态。")
        if drawn:
            marked = self.visual_pdf({"reject_marked": "封面“否决 Reject”列中，尺寸、材料、性能、试装任一行有实际的勾或叉标记；空框为false。"})["reject_marked"]
            if marked is True:
                return "NG", "封面否决列有实际标记。"
            if marked is not False:
                raise UnknownFact("封面否决列的勾选尚未核实，需人工确认原件。")
        remarks = cover.field(("UAES批准的前提/备注", "UAES 批准的前提/备注"), table=page, inline=True)
        full = "完全认可" in remarks
        conditional = any(term in remarks for term in ("偏差认可", "条件认可", "超差", "不合格", "整改"))
        if full and conditional or not full and not conditional:
            raise UnknownFact("封面首要备注无法唯一归类或内容冲突，需人工复核。")
        if full:
            signed = self.cover_signatures()
            if signed is None:
                return self.visual_result(
                    {"signed_dates": "封面组长Team Leader、审核Checked by、批准Approved by三处实际签字及各自日期全部齐全，且组长日期≤审核日期≤批准日期。"}, "完全认可的三方实际签字及递增日期"
                )
            missing = [role for role, (name, day) in signed.items() if not name or day is None]
            if missing:
                return "NG", "封面" + "、".join(missing) + "缺少签名或日期。"
            days = [day for _name, day in signed.values()]
            if days != sorted(days):
                return "NG", "封面签字日期不是组长≤审核≤批准：" + "，".join(f"{role}{name} {day}" for role, (name, day) in signed.items())
            return "PASS", "封面完全认可，" + "，".join(f"{role}{name} {day}" for role, (name, day) in signed.items()) + "，日期递增。"
        deadline_matches = re.findall(r"(?:截止|临时偏差至)\s*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2})|(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2})\s*(?:日)?前完成", remarks)
        dates = [normalize_excel_date(left or right) for left, right in deadline_matches]
        if not dates or any(item is None for item in dates):
            raise UnknownFact("封面未能唯一读取承诺最晚期限；预计整改时间不能代替承诺。")
        deadline = max(item for item in dates if item is not None)
        if deadline <= date.today():
            return "NG", f"封面条件认可时间({deadline})早于或等于当前({date.today()})，请确认整改是否逾期或已闭环。"
        related = cover if cover.tables(("偏差认可汇总",)) else self.related(("偏差认可汇总",), same=("part", "name"))
        # Same-document deviation rows still require their own identity.
        for aliases in [("零件号", "零件编号"), ("零件名称",)]:
            if key(cover.field(aliases, table=page)) != key(related.field(aliases, title=("偏差认可汇总",))):
                raise UnknownFact("偏差汇总与封面零件身份不一致。")
        deviations = related.rows({"item": ("偏差项",), "assessment": ("UAES评估确认",), "needed": ("是否需要偏差",), "remarks": ("备注",)}, title=("偏差认可汇总",), anchor=("item",))
        for row in deviations:
            # Read required fields even when date is invalid, preserving the
            # actual row evidence rather than a document-level guess.
            if not row["assessment"] or not row["needed"]:
                return "NG", "偏差认可汇总的UAES评估确认或是否需要偏差字段为空。"
            matched = re.search(r"临时偏差至\s*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2})", str(row["remarks"]))
            value = normalize_excel_date(matched.group(1)) if matched else None
            if value is None:
                raise UnknownFact("偏差行未能可靠读取临时偏差有效期：" + str(row["item"]))
            if value <= date.today():
                return "NG", f"{row['item']}偏差项有效期({value})已过期，请确认已更新批准或整改闭环。"
            if value != deadline:
                return "NG", f"封面条件认可时间({deadline})与偏差汇总认可清单承诺时间({value})不一致，请复核。"
        return "PASS", "条件认可封面承诺期限与全部偏差行有效期一致且未过期。"

"""UAES primary cover decision, dates and conditional deviation reconciliation."""

import re
from datetime import date

from audit.checkers.v3.excel_agent import rule
from audit.engine.checkers.date_utils import normalize_excel_date

from .native import UaesSourceAgent, UnknownFact, key


class UaesApprovalAgent(UaesSourceAgent):
    @rule("UAES封面首要备注与实际否决签批及偏差期限")
    def rule_1(self):
        cover = self.native()
        rows = cover.rows({"item": ("项目", "检验项目", "试验项目"), "reject": ("否决", "Reject")}, title=("封面", "cover"), anchor=("item",))
        seen = set()
        for row in rows:
            name = str(row["item"])
            for category in ("尺寸", "材料", "性能", "试装"):
                if category in name:
                    seen.add(category)
                    if re.search(r"[×xX☑✓√✔]", str(row["reject"] or "")):
                        return "NG", "封面否决列有实际标记：" + name
        if len(seen) != 4:
            raise UnknownFact("尚不能完整识别尺寸、材料、性能、试装的否决列状态。")
        remarks = cover.field(("UAES批准的前提/备注", "UAES 批准的前提/备注"), title=("封面", "cover"))
        full = "完全认可" in remarks
        conditional = any(term in remarks for term in ("偏差认可", "条件认可", "超差", "不合格", "整改"))
        if full and conditional or not full and not conditional:
            raise UnknownFact("封面首要备注无法唯一归类或内容冲突，需人工复核。")
        if full:
            return self.visual_result(
                {"signed_dates": "封面组长Team Leader、审核Checked by、批准Approved by三处实际签字及各自日期全部齐全，且组长日期≤审核日期≤批准日期。"}, "完全认可的三方实际签字及递增日期"
            )
        deadline_matches = re.findall(r"(?:截止|临时偏差至)\s*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2})|(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2})\s*(?:日)?前完成", remarks)
        dates = [normalize_excel_date(left or right) for left, right in deadline_matches]
        if not dates or any(item is None for item in dates):
            raise UnknownFact("封面未能唯一读取承诺最晚期限；预计整改时间不能代替承诺。")
        deadline = max(item for item in dates if item is not None)
        if deadline <= date.today():
            return "NG", f"封面条件认可时间({deadline})早于或等于当前({date.today()})，请确认整改是否逾期或已闭环。"
        related = cover if cover.tables(("偏差认可汇总",)) else self.related(("偏差认可汇总",), require_name=True)
        # Same-document deviation rows still require their own identity.
        for aliases in [("零件号", "零件编号"), ("零件名称",)]:
            if key(cover.field(aliases, title=("封面", "cover"))) != key(related.field(aliases, title=("偏差认可汇总",))):
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

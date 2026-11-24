"""UAES U14 candidate: order specification (BV), UAES v1.0 revision 3 (ORD:1).

A leaf carries only checker.py and resources.py and may not import the UAES implementation. resources.py
declares the UAES controlled-document reader as the carrier; at run time `Audit` extends the declared reader
class with the revision 3 clause method below, reusing its controlled-stamp reading. Verdicts are decided in
code; the image model only reads stamps, seals and signatures off the original.
"""

from __future__ import annotations

import sys
from typing import Any

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, RuleContext
from resources import RESOURCE

# PDF pages whose text names a sign-off or a seal; the first and last page are read as well.
# (編制/批准 run in every page footer of a BV and do not single out a page.)
SIGN_WORDS = ("签字", "签名", "签章", "盖章", "受控", "供方", "需方", "Signature")

FIELDS = {
    "controlled_stamp": "文件中存在真实盖上的受控章；只有“受控章/受控/盖章处”文字标签、空白盖章栏时不算。",
    "contract_seal": "供应商（供方）签署栏处盖有真实的合同章；只有“合同章/盖章处”文字或空白盖章栏不算。",
    "supplier_signed": "图中有供方（供应商）的责任签署栏，并且该栏有对应负责人的签字。没有供方签署栏或栏内未签为false。",
    "customer_signed": "图中有需方（UAES/联合汽车电子）的责任签署栏，并且该栏有对应负责人的签字。没有需方签署栏或栏内未签为false。",
    "blank_slot": "图中存在已设置却空白未签的责任签署栏位（未使用的整行空白修订记录行不算）。存在为true。",
}


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


class OrderClauses:
    """ORD:1 over the declared UAES controlled-document reader (`READER` is its module)."""

    READER: Any = None

    def observe(self) -> tuple[dict[str, Any], set[str], str]:
        """All image facts in one reading; a long PDF by its first, last and sign-off pages (ten at most).

        Returns the facts, those the model could not make out, and the pages left unread ("" when none)."""
        path = str(self.audit_file.file_url)
        if not path.lower().endswith(".pdf"):
            values, unreadable = self._from_legacy(lambda agent: agent.visual_observation(FIELDS))
            return values, unreadable, ""
        import fitz

        with fitz.open(path) as document:
            count = len(document)
            pages = [page.number + 1 for page in document if any(word in page.get_text() for word in SIGN_WORDS)]
        chosen = list(range(1, count + 1)) if count <= 10 else sorted({1, count, *pages})[:10]

        def read(agent: Any) -> tuple[dict[str, Any], set[str]]:
            from audit.engine.checkers.ocr_visual_facts import unreadable_facts
            from audit.engine.checkers.visual_facts import VisualFactsUnavailable, read_original_pdf_visual_facts

            if not hasattr(agent, "_original_page_cache"):
                agent._original_page_cache = {}
            try:
                values: dict[str, Any] = {}
                with unreadable_facts() as seen:
                    # All five facts in one request over the chosen pages: one request per fact ran past the
                    # clause's three-minute limit on a 17-page BV. A failed request leaves every fact open.
                    try:
                        values = read_original_pdf_visual_facts(agent.audit_file, FIELDS, agent.witness, operation="uaes_original_visual_facts", cache=agent._original_page_cache, source_pages=chosen)
                    except VisualFactsUnavailable:
                        raise
                    except Exception:
                        values = dict.fromkeys(FIELDS)
                        self.__dict__["_failed_facts"] = set(FIELDS)
            except VisualFactsUnavailable as exc:
                raise unable(str(exc)) from None
            return values, {field for field in seen if values.get(field) is None}

        values, unreadable = self._from_legacy(read)
        unread = "" if len(chosen) == count else f"原件共{count}页，只读了第{'、'.join(map(str, chosen))}页"
        return values, unreadable, unread

    def drawn_stamp(self) -> str | None:
        """A spreadsheet picture that draws the words 受控 is the stamp itself (the UAES reader's own reading)."""
        if not str(self.audit_file.file_url).lower().endswith((".xlsx", ".xlsm", ".xls")):
            return None
        facts = self.legacy().native()
        for _table, _visual, text in facts.drawn_texts():
            if "受控" in "".join(str(text).split()):
                return text.strip()
        return None

    @rule("订货规范受控章与供需双方签署闭环")
    def ord_1(self, ctx: RuleContext):
        values, unreadable, unread = self.observe()
        problems: list[str] = []
        drawn = self.drawn_stamp()
        if drawn or values["controlled_stamp"] is True:
            stamp = "原件盖有受控章" + (f"（图片内文字“{drawn}”）" if drawn else "")
        elif values["contract_seal"] is True:
            stamp = "供应商签署栏盖有合同章，与受控公章同等认定"
        elif unreadable & {"controlled_stamp", "contract_seal"}:
            problems.append("原件上的印章无法辨认是否为受控章或合同章，请人工复核")
            stamp = ""
        elif values["controlled_stamp"] is False and values["contract_seal"] is False and not unread:
            problems.append("没有受控章（供应商签署栏也没有合同章）")
            stamp = ""
        else:
            stamp = ""
        unsigned = [side for side, name in (("供方", "supplier_signed"), ("需方", "customer_signed")) if values[name] is False]
        if unsigned:
            problems.append("、".join(unsigned) + "的责任签署栏没有对应负责人签字，签署不闭环")
        if values["blank_slot"] is True:
            problems.append("存在空白未签的责任签署栏位，签署不闭环")
        if problems:
            return "NG", "订货规范" + "；".join(problems) + (f"（{unread}）" if unread else "") + "。"
        failed = self.__dict__.pop("_failed_facts", set())
        if failed:
            raise GuardStop(Decision.ERR, "原件图像读取请求失败（" + "、".join(sorted(failed)) + "），尚未取得业务结论。")
        if not stamp:
            raise unable("受控章（或供应商签署栏的合同章）未能从原件核实" + (f"，{unread}" if unread else "") + "，需人工复核。")
        if any(values[name] is None for name in ("supplier_signed", "customer_signed", "blank_slot")):
            raise unable(f"{stamp}；供需双方责任签署栏的签字尚未能从原件全部核实" + (f"（{unread}）" if unread else "") + "，需人工复核。")
        return "PASS", f"{stamp}；供需双方责任签署栏均有负责人签字，没有空白未签栏位。"


class Audit(AgentAudit):
    """Runs the declared UAES reader extended with this leaf's clause method (see the module docstring)."""

    CLAUSES: dict[str, type] = {"UaesControlledDocumentAgent": OrderClauses}

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

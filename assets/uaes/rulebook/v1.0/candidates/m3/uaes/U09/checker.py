"""UAES U09 candidate: packaging plan, UAES v1.0 revision 3 (PKG:1).

A leaf carries only checker.py and resources.py and may not import the UAES implementation. resources.py
declares the UAES packaging readers as carriers; at run time `Audit` extends the declared reader class with
the revision 3 clause method below, reusing the reader's title and content-block reading. Verdicts are decided
in code; the image model only reads signatures and seals off the original.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from typing import Any

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, RuleContext
from resources import RESOURCE

# The supplier's sign-off columns the clause names; UAES's approval column may stay blank and is not read.
SIGNERS = ("编制", "审核", "批准")
SEAL = "供应商的编制、审核、批准签署区（含共用盖章处）至少有一处真实的受控印章或公章（电子签章图层也算）；只有“盖章处”文字或空白不算。"


def compact(value: Any) -> str:
    """Comparable text: width-folded, spaces removed, case-folded, trailing colon dropped (as the UAES reader)."""
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold().rstrip(":：")


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


def signer(text: str) -> str | None:
    """A sign-off label: the name alone, with 人/者 or English, never a sentence that mentions it."""
    bare = re.sub(r"[\W_a-z0-9]+", "", compact(text))
    return next((name for name in SIGNERS if bare in {name, name + "人", name + "者"}), None)


def sign_fields(names: list[str]) -> dict[str, str]:
    fields = {f"sign_{index}": f"供应商侧“{name}”签署栏有手写签字或电子手写签名图层；纯打印体姓名、空白为false。" for index, name in enumerate(names)}
    return {**fields, "seal": SEAL}


def judge_signatures(names: list[str], values: dict[str, Any]) -> tuple[str, str]:
    unsigned = [name for index, name in enumerate(names) if values.get(f"sign_{index}") is False]
    problems = []
    if unsigned:
        problems.append("、".join(f"“{name}”" for name in unsigned) + "栏没有手写签字")
    if values.get("seal") is False:
        problems.append("编制、审核、批准签署区没有受控印章或公章")
    if problems:
        return "NG", "；".join(problems)
    if any(value is None for value in values.values()):
        return "UNABLE", "编制、审核、批准签署栏的签字或印章尚未能从原件全部核实，需人工复核"
    return "PASS", "编制、审核、批准签署栏均有手写签字，签署区有印章"


class PackagingClauses:
    """PKG:1 over the declared UAES packaging reader (`READER` is its module)."""

    READER: Any = None

    def signers(self, ctx: RuleContext) -> list[str]:
        """The supplier sign-off columns the original writes: cells, or lines of a PDF's text."""
        found: list[str] = []
        for sheet in self.sheets(ctx):
            for cell in self.cells(sheet):
                name = signer(self.text(cell))
                if name and name not in found:
                    cell.value()
                    found.append(name)
        if str(self.audit_file.file_url).lower().endswith(".pdf"):
            import fitz

            with fitz.open(str(self.audit_file.file_url)) as document:
                for line in "".join(page.get_text() for page in document).splitlines():
                    for part in re.split(r"[:：\s/]+", line):
                        name = signer(part)
                        if name and name not in found:
                            found.append(name)
        return [name for name in SIGNERS if name in found]

    @rule("包装方案标题、六个内容块与供应商签署")
    def pkg_1(self, ctx: RuleContext):
        # The title "contains" 包装方案 and the six blocks: the reader's own reading (rule_1).
        try:
            content_status, content = super().rule_1(ctx)
        except GuardStop as stop:
            content_status, content = stop.status, stop.message
        names = self.signers(ctx)
        if not names:
            sign_status, sign = self.lacking("供应商编制、审核、批准签署栏")
        else:
            try:
                values, _unreadable = self.visual_values(sign_fields(names))
                sign_status, sign = judge_signatures(names, values)
            except GuardStop as stop:
                sign_status, sign = stop.status, "签字和印章未能读取：" + stop.message.rstrip("。")
            missing = [name for name in SIGNERS if name not in names]
            if missing and sign_status != "NG":
                sign_status, sign = self.lacking("、".join(f"“{name}”" for name in missing) + "签署栏")
        problems = [text.rstrip("。") for status, text in ((content_status, content), (sign_status, sign)) if status == "NG"]
        if problems:
            return "NG", "包装方案" + "；".join(problems) + "。"
        if "ERR" in (content_status, sign_status):
            raise GuardStop(Decision.ERR, "；".join(text.rstrip("。") for status, text in ((content_status, content), (sign_status, sign)) if status != "PASS") + "。")
        if "UNABLE" in (content_status, sign_status):
            raise unable("；".join(text.rstrip("。") for status, text in ((content_status, content), (sign_status, sign)) if status == "UNABLE") + "。")
        return "PASS", content.rstrip("。") + "；" + sign + "。"

    def lacking(self, what: str) -> tuple[str, str]:
        """NG when the whole original was read as text and does not write `what`; else UNABLE."""
        try:
            return self.absent(what)
        except GuardStop as stop:
            return stop.status, stop.message

    def visual_values(self, fields: dict[str, str]) -> tuple[dict[str, Any], set[str]]:
        return self._from_legacy(lambda agent: agent.visual_observation(fields))


class PackagingDocxClauses:
    """PKG:1 over the declared UAES Word packaging reader."""

    READER: Any = None

    @rule("包装方案标题、六个内容块与供应商签署")
    def pkg_1(self):
        status, content = super().rule_1()
        facts = self.native()
        names = [name for name in SIGNERS if facts.contains(name)]
        if not names:
            return "NG", "包装方案原件中没有供应商编制、审核、批准签署栏。"
        values, _unreadable = self.visual_observation(sign_fields(names))
        sign_status, sign = judge_signatures(names, values)
        missing = [name for name in SIGNERS if name not in names]
        if sign_status == "NG" or missing:
            return "NG", "包装方案" + "；".join([*([sign] if sign_status == "NG" else []), *(["原件中没有" + "、".join(missing) + "签署栏"] if missing else [])]) + "。"
        if sign_status == "UNABLE":
            return "UNABLE", sign + "。"
        return status, content.rstrip("。") + "；" + sign + "。"


class Audit(AgentAudit):
    """Runs the declared UAES reader extended with this leaf's clause method (see the module docstring)."""

    CLAUSES: dict[str, type] = {"UaesPackagingAgent": PackagingClauses, "UaesPackagingDocxAgent": PackagingDocxClauses}

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

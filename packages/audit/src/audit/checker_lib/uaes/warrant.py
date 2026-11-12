"""UAES part submission warrant (PSW).

The only UAES PSW original is a one-page scan (CFG-1001 with some typed overlays): its fields, boxes and
signature exist only as an image. The page is transcribed once into a fixed JSON schema by the image model;
every clause decides from that transcription in code. The seal is located by Paddle's seal detection.
A text-layer warrant, whose layout this checker has not declared, is not guessed at.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.rule_engine import GuardStop, RuleContext

from .excel import UaesExcelAgent, unable
from .resources import combine
from .resources import warrant as resources

_OTHER = ("other", "其它", "其他")


class _Reading:
    """One clause's decision over the transcription: definite violations, and what could not be read."""

    def __init__(self, agent: UaesWarrantAgent):
        self.agent, self.facts = agent, agent.transcription()
        self.failed: list[str] = []
        self.unknown: list[str] = []

    def present(self, name: str, label: str) -> bool:
        present = (self.facts.get(name) or {}).get("present")
        if present is False:
            self.failed.append(f"缺少{label}")
        elif present is None:
            self.unknown.append(label)
        return present is True

    def field(self, name: str, label: str) -> str | None:
        if not self.present(name, label):
            return None
        value = self.facts[name].get("value")
        if value is None:
            self.unknown.append(label)
            return None
        if not value.strip():
            self.failed.append(f"{label}为空")
            return None
        self.agent.note(name, value)
        return value

    def choice(self, name: str, label: str) -> list[dict[str, Any]] | None:
        """The checked options of a choice; None when the choice or any of its marks cannot be read."""
        if not self.present(name, label):
            return None
        options = self.facts[name].get("options") or []
        if not options or any(option.get("checked") is None for option in options):
            self.unknown.append(f"{label}的勾选")
            return None
        checked = [option for option in options if option["checked"] is True]
        self.agent.note(name, "、".join(str(option.get("label") or "") for option in checked) or "未勾选")
        return checked

    def one_of(self, name: str, label: str) -> None:
        checked = self.choice(name, label)
        if checked is not None and len(checked) != 1:
            self.failed.append(f"{label}{'未勾选' if not checked else '勾选了多项'}")

    def decided(self, passed: str) -> tuple[str, str]:
        if self.failed:
            return "NG", "零件提交保证书" + "；".join(self.failed) + "。"
        if self.unknown:
            raise unable(f"未能从零件提交保证书原件图像辨识{'、'.join(self.unknown)}，需人工复核。")
        return "PASS", passed


class UaesWarrantAgent(UaesExcelAgent):
    DOCUMENT_MODULE = resources
    EXTRA_RESOURCE = combine(resources.DOCUMENT)

    def _load_workbook(self):
        # Nothing on a warrant is read as a table, so the original is not converted.
        if self._workbook_cache is None:
            from openpyxl import Workbook

            self._workbook_cache = Workbook()
        return self._workbook_cache

    def transcription(self) -> dict[str, Any]:
        """The scanned page as the warrant schema, read once for all clauses."""
        if "_transcription" not in self.__dict__:
            try:
                self._transcription: Any = self._transcribe()
            except GuardStop as stop:
                self._transcription = stop
        if isinstance(self._transcription, GuardStop):
            raise self._transcription
        return self._transcription

    def _transcribe(self) -> dict[str, Any]:
        path = str(self.audit_file.file_url)
        if not path.lower().endswith(".pdf"):
            raise unable("检查程序尚未声明此格式零件提交保证书的读取方式，需人工复核。")
        if not self.unread_pages():
            raise unable("零件提交保证书是文字版PDF，检查程序尚未声明此版式的字段读取方式，需人工复核。")
        import fitz

        with fitz.open(path) as document:
            if len(document) != 1:
                raise unable(f"零件提交保证书有{len(document)}页，检查程序尚未声明多页保证书的读取范围，需人工复核。")
            image = document[0].get_pixmap(dpi=200).tobytes("png")
        from audit.engine.checkers.ocr_visual_facts import request_ocr_visual_facts
        from audit.engine.runtime import get_runtime

        runtime = get_runtime()
        hook = getattr(runtime, "read_ocr_visual_facts", None)
        if runtime is not None and not callable(hook):
            raise unable("当前运行环境不提供原件图像转写，零件提交保证书尚未核实，需人工复核。")
        request = hook if callable(hook) else request_ocr_visual_facts
        self._digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        return self._from_legacy(lambda _agent: request(images=[image], prompt=resources.PROMPT, response_schema={"name": "psw", "schema": resources.SCHEMA}))

    def note(self, fact: str, value: str) -> None:
        """A transcribed value is evidence of page 1 of this original."""
        locator = {"kind": "pdf_page", "page": 1, "source_sha256": self._digest, "engine": "ocr_llm", "fact": fact}
        self.legacy().witness(locator, value, visual=True)

    def seal(self) -> bool | None:
        """Paddle's seal detection: True where it locates a seal; None otherwise, since no detection proves no seal."""
        from audit.engine.checkers.visual_facts import VisualFactsUnavailable, _read_paddle_visual_facts

        def read(agent: Any) -> bool | None:
            cache = agent.__dict__.setdefault("_paddle_page_cache", {})
            return _read_paddle_visual_facts(self.audit_file, {"seal": "印章"}, agent.witness, cache=cache)["seal"]

        try:
            return self._from_legacy(read)
        except VisualFactsUnavailable as exc:
            raise unable(f"{exc}") from None

    @rule("PSW基础身份两个工程日期及法规唯一选择")
    def rule_1(self, ctx: RuleContext):
        reading = _Reading(self)
        for name, label in resources.IDENTITY.items():
            reading.field(name, label)
        reading.one_of(*resources.SAFETY)
        return reading.decided("零件身份、两个工程更改及其日期均已填写，安全及政府条例恰好勾选一项。")

    @rule("供方和买方资料材料申报及IMDS")
    def rule_2(self, ctx: RuleContext):
        reading = _Reading(self)
        if reading.present(*resources.SUPPLIER_AREA):
            reading.field(*resources.SUPPLIER_NAME)
        if reading.present(*resources.CUSTOMER_AREA):
            for name, label in resources.CUSTOMER.items():
                reading.field(name, label)
        reading.one_of(*resources.SUBSTANCES)
        reading.field(*resources.IMDS)
        return reading.decided("供方公司名称、买方三项资料、材料信息唯一勾选与IMDS均已填写。")

    @rule("供方代表手写签字和真实印章")
    def rule_3(self, ctx: RuleContext):
        reading = _Reading(self)
        name, label = resources.SIGNATURE
        if (self.transcription().get(name) or {}).get("present") is False:
            return "NG", "零件提交保证书中缺少供方代表签字字段"
        if reading.present(name, label):
            handwritten = self.transcription()[name].get("handwritten")
            if handwritten is False:
                reading.failed.append(f"{label}不是手写签名")
            elif handwritten is None:
                reading.unknown.append(f"{label}是否手写")
            else:
                self.note(name, self.transcription()[name].get("value") or "手写签名")
        if reading.failed or reading.unknown:
            return reading.decided("")
        if self.seal() is not True:
            raise unable("零件提交保证书有供方代表手写签字；Paddle印章检测未在原页定位到印章，未检出不能证明没有印章，需人工复核。")
        return "PASS", "零件提交保证书有供方代表手写签字，Paddle在原页定位到印章。"

    @rule("提交原因选择与其它说明")
    def rule_4(self, ctx: RuleContext):
        reading = _Reading(self)
        name, label = resources.REASON
        if (self.transcription().get(name) or {}).get("present") is False:
            return "NG", "缺少提交原因"
        checked = reading.choice(name, label)
        if checked is not None:
            if not checked:
                return "NG", "请勾选提交原因"
            if any(word in str(option.get("label") or "").casefold() for option in checked for word in _OTHER):
                reading.field(*resources.REASON_OTHER)
        return reading.decided("提交原因已勾选；勾选“其它”时其说明已填写。")

    @rule("提交等级唯一实际选择")
    def rule_5(self, ctx: RuleContext):
        reading = _Reading(self)
        name, _label = resources.LEVEL
        if (self.transcription().get(name) or {}).get("present") is False:
            return "NG", "缺少要求提交的等级"
        reading.one_of(*resources.LEVEL)
        return reading.decided("要求提交的等级恰好勾选一项。")

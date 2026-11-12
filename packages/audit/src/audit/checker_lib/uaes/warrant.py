"""UAES part submission warrant (PSW): fields, selections, sign-off and seal of a scanned warrant.

The only UAES PSW original is a scanned page (CFG-1001 with some typed overlays): its fields, boxes,
signature and seal exist only as an image, so they are read from the image of the original. A PSW
whose layout this checker has not declared is not guessed at.
"""

from __future__ import annotations

import json

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.rule_engine import RuleContext

from .excel import UaesExcelAgent, unable
from .resources import combine
from .resources import warrant as resources


class UaesWarrantAgent(UaesExcelAgent):
    DOCUMENT_MODULE = resources
    EXTRA_RESOURCE = combine(resources.DOCUMENT)

    def precheck(self):
        """Preparation facts still come from the snapshot reader."""
        from .native import UaesSourceAgent
        from .precheck import structural_facts

        return structural_facts(UaesSourceAgent(self.audit_file, self.rule, group_context=self.group_context))

    def _load_workbook(self):
        # Nothing on a warrant is read as a table, so the original is not converted.
        if self._workbook_cache is None:
            from openpyxl import Workbook

            self._workbook_cache = Workbook()
        return self._workbook_cache

    def _execute_rule_check(self, rule_no: int):
        result = super()._execute_rule_check(rule_no)
        from audit.engine.checkers.paddle_evidence import observe_shadow

        if 0 < rule_no <= len(self.declared_rules()):
            observe_shadow(self, self.declared_rules()[rule_no - 1][0])
        return result

    def scanned(self) -> None:
        """Proceed only on a scanned warrant: a page that is an image, or has no text layer at all."""
        path = str(self.audit_file.file_url)
        if not path.lower().endswith(".pdf"):
            raise unable("检查程序尚未声明此格式零件提交保证书的读取方式，需人工复核。")
        import fitz

        with fitz.open(path) as document:
            scanned = any(not page.get_text().strip() or any(abs(fitz.Rect(image["bbox"]) & page.rect) >= 0.8 * abs(page.rect) for image in page.get_image_info()) for page in document)
        if not scanned:
            raise unable("零件提交保证书是文字版PDF，检查程序尚未声明此版式的字段读取方式，需人工复核。")

    def read(self, fields: dict[str, str], description: str) -> tuple[str, str]:
        self.scanned()
        return self.visual(fields, description)

    @rule("PSW基础身份两个工程日期及法规唯一选择")
    def rule_1(self, ctx: RuleContext):
        return self.read(resources.IDENTITY, "零件身份、两个工程更改及其日期、安全及政府条例唯一勾选")

    @rule("供方和买方资料材料申报及IMDS")
    def rule_2(self, ctx: RuleContext):
        return self.read(resources.PARTIES, "供方制造厂资料、买方资料、材料信息唯一勾选与IMDS")

    @rule("供方代表手写签字和真实印章")
    def rule_3(self, ctx: RuleContext):
        status, message = self.read(resources.SIGN_OFF, "供方代表签字栏及手写签字")
        if status != "PASS":
            return status, message
        seal = self._from_legacy(lambda agent: agent.visual_pdf(resources.SEAL)).get("seal")
        if seal is False:
            return "NG", "零件提交保证书有供方代表手写签字，但原件上没有实际印章。"
        if seal is not True:
            raise unable("零件提交保证书上的印章尚未从原件图像核实，需人工复核。")
        # The image reader takes printed logos for stamps on this form; a stamp counts once the seal
        # detector has located it on the page.
        if not any(json.loads(source.locator).get("element") == "seal" for source in self.legacy()._evidence_sources):
            raise unable("图像读取认为有印章，但印章检测未在原页定位到印章（表头印刷标志可能被误认），需人工复核。")
        return "PASS", "已从原件核实供方代表手写签字和实际印章。"

    @rule("提交原因选择与其它说明")
    def rule_4(self, ctx: RuleContext):
        return self.read(resources.REASON, "提交原因勾选及其它说明")

    @rule("提交等级唯一实际选择")
    def rule_5(self, ctx: RuleContext):
        return self.read(resources.LEVEL, "要求提交的等级唯一勾选")

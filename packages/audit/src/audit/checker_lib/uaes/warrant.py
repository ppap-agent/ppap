"""UAES part submission warrant (PSW).

A scanned warrant is not read: its fields, boxes, signature and seal exist only as an image. The only UAES
PSW original is such a scan (CFG-1001 with some typed overlays). A warrant whose layout this checker has not
declared is not guessed at. Every clause therefore reports what keeps it from being read.
"""

from __future__ import annotations

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

    def unread(self, subject: str):
        path = str(self.audit_file.file_url)
        if not path.lower().endswith(".pdf"):
            raise unable(f"检查程序尚未声明此格式零件提交保证书的读取方式，{subject}需人工复核。")
        if pages := self.unread_pages():
            raise unable(f"零件提交保证书第{'、'.join(map(str, pages))}页是扫描件，检查程序不识别扫描内容，{subject}需人工复核。")
        raise unable(f"零件提交保证书是文字版PDF，检查程序尚未声明此版式的字段读取方式，{subject}需人工复核。")

    @rule("PSW基础身份两个工程日期及法规唯一选择")
    def rule_1(self, ctx: RuleContext):
        return self.unread("零件身份、两个工程更改及其日期、安全及政府条例勾选")

    @rule("供方和买方资料材料申报及IMDS")
    def rule_2(self, ctx: RuleContext):
        return self.unread("供方制造厂资料、买方资料、材料信息勾选与IMDS")

    @rule("供方代表手写签字和真实印章")
    def rule_3(self, ctx: RuleContext):
        return self.unread("供方代表手写签字和印章")

    @rule("提交原因选择与其它说明")
    def rule_4(self, ctx: RuleContext):
        return self.unread("提交原因勾选及其它说明")

    @rule("提交等级唯一实际选择")
    def rule_5(self, ctx: RuleContext):
        return self.unread("要求提交的等级勾选")

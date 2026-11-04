"""UAES record-specific requirements; textual labels never prove signatures."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from audit.checkers.v3.excel_agent import rule
from audit.engine.checkers.date_utils import normalize_excel_date

from .native import MissingFact, UaesSourceAgent, UnknownFact


class UaesPackagingAgent(UaesSourceAgent):
    PRECHECK_METHODS = {"U09:1:d4a3aece8ee5d551fa27": "rule_1"}

    @rule("包装方案标题与六个内容块")
    def rule_1(self):
        facts = self.native()
        facts.require_title("包装方案")
        for phrase in ("零件基础信息", "供应商基础信息", "包装设计的基本要求", "针对该零件包装的特殊要求", "包装方案信息", "包装材料清单"):
            facts.require_text(phrase)
        return "PASS", "已读取包装方案标题及原规则要求的六个内容块。"


class UaesControlledDocumentAgent(UaesSourceAgent):
    @rule("原件实际受控章")
    def rule_1(self):
        return self.controlled_stamp()


class UaesEngineeringHistoryAgent(UaesSourceAgent):
    PRECHECK_METHODS = {"U11:1:cf088001602b522ab78a": "rule_1"}

    @rule("工程更改一览表及五类字段")
    def rule_1(self):
        facts = self.native()
        facts.require_text("工程更改一览表")
        aliases = {
            "项目": ("项目", "项目名称", "项目代号"),
            "零件号": ("零件号", "物料编号", "零件编号"),
            "变更内容": ("变更内容简述", "变更内容", "更改内容"),
            "原因": ("变更原因", "更改原因"),
            "时间": ("时间", "变更日期", "更改日期", "日期"),
        }
        table = facts.table(("工程更改",))
        try:
            facts.columns(table, aliases)
        except MissingFact as exc:
            # The source explicitly permits semantically equivalent aliases.
            # An unrecognized spelling is not proof that the field is absent.
            raise UnknownFact("尚不能完整确认工程更改表字段及合法别名，需人工核对。") from exc
        return "PASS", "工程更改表及项目、零件号、变更内容、原因、时间已核实。"


class UaesTraceabilityAgent(UaesSourceAgent):
    PRECHECK_METHODS = {"U18:1:67b6f6fe04be4c627598": "rule_1"}

    @rule("供应商零件追溯五个区域须有实际内容")
    def rule_1(self):
        facts = self.native()
        facts.require_text("供应商零件追溯")
        for names in [("基本信息",), ("分供方追溯",), ("生产过程追溯",), ("交付追溯",), ("追溯状态总结",)]:
            facts.field(names, title=("供应商零件追溯",))
        return "PASS", "已读取供应商零件追溯五个区域的对应内容。"


class UaesTrialAgent(UaesSourceAgent):
    @rule("试装流转记录和两类实际勾选结论")
    def rule_1(self):
        facts = self.native()
        for phrase in ("流转记录", "试装结论", "检验结论", "流程结束", "流程完成"):
            facts.require_text(phrase)
        trial = facts.field(("试装结论",))
        inspection = facts.field(("检验结论",))

        def chosen(value, positive, negative):
            compact = str(value).replace(" ", "")
            yes = bool(re.search(r"(?:[☑✓√✔■]" + positive + "|" + positive + r"[☑✓√✔■])", compact))
            no = bool(re.search(r"(?:[☑✓√✔■]" + negative + "|" + negative + r"[☑✓√✔■])", compact))
            if yes and not no:
                return True
            if no and not yes:
                return False
            return None

        results = [chosen(trial, "通过", "未通过"), chosen(inspection, "合格", "不合格")]
        if False in results:
            return "NG", "试装或检验结论选中了未通过/不合格。"
        if None in results:
            return "UNABLE", "尚不能唯一确认两个结论的实际勾选状态，文字标签不代表已选择。"
        return "PASS", "已核实流程结束/完成和两类通过结论的实际勾选。"


class UaesLabQualificationAgent(UaesSourceAgent):
    @rule("实验室认可标识和真实截止日期")
    def rule_1(self):
        facts = self.native()
        facts.require_text("中国合格评定国家认可委员会")
        facts.require_text("CNAS")
        value = facts.field(("截止日期", "有效期至", "有效期截止日期", "证书有效期至"))
        deadline = normalize_excel_date(value)
        if deadline is None:
            raise UnknownFact("认可证书的截止日期未能可靠识别，需人工复核。")
        if deadline <= date.today():
            return "NG", "实验室资质已到期，截止日期必须晚于当前日期。"
        message = "资质截止日期晚于当前日期；CNAS标识及认可委员会名称已读取。"
        if self.group_context and any(
            any(term in str(file.file_metadata.get("original_filename", "")) for term in ("试验报告", "验证报告", "DVR", "DV报告", "PV报告")) for file in self.group_context.by_audit_id.values()
        ):
            message += "同批有试验报告，需人工比对测试机构与资质实验室是否一致。"
        status, visual_message = self.visual_result({"cnas_mark": "资质证书存在实际CNAS认可标识，不能仅有提及CNAS或空标识占位。"}, "证书实际CNAS认可标识")
        return status, message + visual_message


class UaesMeasurementComparisonAgent(UaesSourceAgent):
    def precheck(self):
        from .precheck_l1 import precheck_measurement

        return precheck_measurement(self)

    @rule("测量比较报告四方原件签字")
    def rule_1(self):
        facts = self.native()
        facts.require_text("测量比较报告")
        for phrase in ("供应商", "UAES技术中心", "UAES测量部门", "UAES供应商质量开发工程师"):
            facts.require_text(phrase)
        observed = self.visual_pdf({"four_signatures": "测量比较报告底部供应商、UAES技术中心、UAES测量部门、UAES供应商质量开发工程师四处对应区域均有真实签字。不能用预印姓名或其他位置签字代替。"})
        fact = observed["four_signatures"]
        return ("PASS", "四方实际签字已从原件核实。") if fact is True else ("NG", "四方签字不完整。") if fact is False else ("UNABLE", "四方签字尚未核实，需人工复核。")


class UaesPhotoAgent(UaesSourceAgent):
    PRECHECK_METHODS = {"U13:1:bc1f288c8f9d34fe2d17": "rule_1"}

    SUPPORTED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".pdf"})

    @rule("可读取照片载体与人工确认提示")
    def rule_1(self):
        from documents.photo_inputs import validate_photo_input

        path = Path(self.audit_file.file_url)
        result = validate_photo_input(path.name, path.read_bytes())
        message = "需人工确认样品照片是否符合要求；可读取图片不代表照片内容正确。"
        if result["state"] == "valid":
            self.witness({"kind": "file_version"}, f"已安全解码{result['image_count']}幅图像。")
            return "PASS", message
        return ("ERR" if result["state"] == "error" else "UNABLE"), "；".join(result["reasons"]) + "；" + message

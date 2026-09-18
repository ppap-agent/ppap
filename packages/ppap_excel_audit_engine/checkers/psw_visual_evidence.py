"""Shared strict evaluation policy for PSW visual evidence."""

from __future__ import annotations

import json
import logging
from typing import Protocol, Sequence

from ppap_core.contracts import AuditResultItem as AuditResult

logger = logging.getLogger(__name__)

PSW_ALWAYS_VISUAL_RULES = frozenset({3, 5})
PSW_CHECKBOX_DEPENDENT_RULES = frozenset({7, 8, 9, 10})

PSW_RULE_PROMPTS: dict[int, dict[str, str]] = {
    3: {
        "label": "印章检查",
        "classification": "印章信息检查",
        "item": "印章信息检查",
        "system_prompt": "你是一个专业的图片审核员，擅长识别图片中的印章。",
        "user_prompt": (
            "识别“零件提交保证书”中是否包含印章，如果印章中文字的最后两个字为“公司”，则直接通过；"
            "否则继续识别文件中是否包含“部门章授权声明”表格，如果不包含则不通过，否则继续识别；"
            "当“部门章授权声明”表格中有公司章则通过，否则不通过。"
        ),
    },
    5: {
        "label": "经授权签字和盖章检查",
        "classification": "经授权签字和盖章检查",
        "item": "经授权签字和盖章检查",
        "system_prompt": "你是一个专业的图片审核员，擅长识别图片中的印章。",
        "user_prompt": "识别“零件提交保证书”中“经授权的供方代表签字”和“盖章”缺一不可，是则通过，否则不通过。",
    },
    7: {
        "label": "提交原因检查",
        "classification": "提交原因检查",
        "item": "提交原因检查",
        "system_prompt": "你是一个专业的图片审核员，擅长识别图片中的内容。",
        "user_prompt": "“提交原因”区域的选项至少有一个选项被选中，或者“其他-请在下方说明”下的内容不为空则通过，否则不通过。",
    },
    8: {
        "label": "认可情况检查",
        "classification": "认可情况检查",
        "item": "认可情况检查",
        "system_prompt": "你是一个专业的图片审核员，擅长识别图片中的内容。",
        "user_prompt": (
            "识别PSW是否包含“认可情况”字段；“认可情况”是否有且仅有一个选项被勾选；"
            "当“批准”选项被勾选时，“提交结果”区域的“尺寸测量”“外观标准”“材料和功能试验”"
            "“统计过程数据”均被勾选且“这些结果均满足所有图纸和规范要求”也有选项被勾选才通过，否则不通过。"
        ),
    },
    9: {
        "label": "条件批准日期检查",
        "classification": "条件批准日期检查",
        "item": "条件批准日期检查",
        "system_prompt": "你是一个专业的图片审核员，擅长识别图片中的内容。",
        "user_prompt": "当认可情况勾选“条件批准”，继续识别文件右下角日期和当前日期是否在3个月之内，在3个月内则通过，否则不通过。",
    },
    10: {
        "label": "条件批准补充评论检查",
        "classification": "条件批准补充评论检查",
        "item": "条件批准补充评论检查",
        "system_prompt": "你是一个专业的图片审核员，擅长识别图片中的内容。",
        "user_prompt": "当认可情况勾选“条件批准”，且补充评论内容不为空则通过，否则不通过。",
    },
}


class PswVisualAnalyzer(Protocol):
    def __call__(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: list[str],
        json_format: bool,
    ) -> str: ...


class PswVisualRuleEvaluator:
    """Evaluate one PSW visual rule with strict, format-neutral semantics."""

    def __init__(self, analyze: PswVisualAnalyzer) -> None:
        self._analyze = analyze

    @staticmethod
    def _default_suggestions(rule_no: int, status: object) -> str:
        status_text = str(status or "").upper()
        if status_text == "PASS":
            return f"规则{rule_no}检查通过"
        if status_text == "NG":
            return f"规则{rule_no}检查不通过"
        return ""

    def evaluate(
        self,
        rule_no: int,
        images: Sequence[str],
        *,
        source_locations: Sequence[str] = (),
    ) -> AuditResult:
        config = PSW_RULE_PROMPTS[rule_no]
        source_note = ""
        if source_locations:
            source_note = "\n\n视觉证据来源：\n- " + "\n- ".join(source_locations)
        user_prompt = (
            f"{config['user_prompt']}{source_note}\n\n"
            "请根据检查结果，严格按照以下JSON格式返回，不要添加任何额外解释：\n"
            "{\n"
            f'    "classification": "{config["classification"]}",\n'
            f'    "item": "{config["item"]}",\n'
            '    "priority": "P0",\n'
            '    "status": "Pass/NG/ERR",\n'
            '    "suggestions": "修改意见"\n'
            "}"
        )
        response = self._analyze(
            system_prompt=config["system_prompt"],
            user_prompt=user_prompt,
            images=list(images),
            json_format=True,
        )
        try:
            result_dict = json.loads(response.replace("```json", "").replace("```", "").strip())
            if not isinstance(result_dict, dict):
                raise ValueError("visual response must be a JSON object")
            status = str(result_dict.get("status", "") or "").upper()
            if status not in {"PASS", "NG"}:
                result_dict["status"] = "NG"
                result_dict["suggestions"] = "视觉证据识别失败，无法验证必需证据。"
            else:
                result_dict["status"] = status
            if not str(result_dict.get("suggestions", "") or "").strip():
                result_dict["suggestions"] = self._default_suggestions(rule_no, result_dict.get("status"))
            return AuditResult(**result_dict)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("PSW rule %s visual evidence could not be verified: %s", rule_no, exc)
            return AuditResult(
                classification=config["classification"],
                item=config["item"],
                priority="P0",
                status="NG",
                suggestions="视觉证据识别失败，无法验证必需证据。",
            )


__all__ = [
    "PSW_ALWAYS_VISUAL_RULES",
    "PSW_CHECKBOX_DEPENDENT_RULES",
    "PSW_RULE_PROMPTS",
    "PswVisualAnalyzer",
    "PswVisualRuleEvaluator",
]

"""Shared strict evaluation policy for PSW visual evidence."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Protocol, Sequence

from ppap_core.contracts import AuditResultItem as AuditResult

logger = logging.getLogger(__name__)

PSW_ALWAYS_VISUAL_RULES = frozenset({3, 5})
PSW_CHECKBOX_DEPENDENT_RULES = frozenset({7, 8, 9, 10})
_CONDITIONAL_VISUAL_RULES = frozenset({9, 10})
# Same window as the structured PSW rule 9: 0 through 92 days is inside 3 months.
_CONDITIONAL_APPROVAL_WINDOW_DAYS = 92
_UNVERIFIABLE = "视觉证据识别失败，无法验证必需证据。"
_SELECTION_NG = "“认可情况”需且仅需勾选一个选项。"
_MISSING_FIELD_NG = "未找到“认可情况”字段。"
_DATE_MISSING_NG = "无法识别条件批准日期。"
_DATE_WINDOW_NG = "条件批准日期超出3个月。"
_COMMENTS_NG = "条件批准需填写补充评论。"

_CONDITIONAL_OBSERVATION_PROMPT = "查看“认可情况”中的“批准”“条件批准”“拒绝”，以及该区域的日期和补充评论。只报告可见事实，不要判断通过或不通过。"

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
        "system_prompt": "你是一个专业的图片审核员，擅长识别图片中的内容。只报告可见事实，不要判断通过或不通过。",
        "user_prompt": _CONDITIONAL_OBSERVATION_PROMPT,
    },
    10: {
        "label": "条件批准补充评论检查",
        "classification": "条件批准补充评论检查",
        "item": "条件批准补充评论检查",
        "system_prompt": "你是一个专业的图片审核员，擅长识别图片中的内容。只报告可见事实，不要判断通过或不通过。",
        "user_prompt": _CONDITIONAL_OBSERVATION_PROMPT,
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


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "yes", "y", "1"}:
            return True
        if token in {"false", "no", "n", "0"}:
            return False
    return None


def _parse_approval_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"null", "none", "n/a", "na", "无"}:
        return None
    match = re.search(r"(20\d{2})\D{0,3}(1[0-2]|0?[1-9])\D{0,3}(3[01]|[12]\d|0?[1-9])", text)
    if match is None:
        return None
    year, month, day = (int(group) for group in match.groups())
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def decide_conditional_visual_rule(
    rule_no: int,
    observation: dict[str, object],
    *,
    today: date,
) -> tuple[str, str]:
    """Apply the structured PSW rule 9/10 order to reported checkbox facts.

    The model does not choose PASS or NG. A missing or unreadable fact fails closed.
    """

    if rule_no not in _CONDITIONAL_VISUAL_RULES:
        raise ValueError(f"rule {rule_no} is not a conditional visual rule")

    visible = _as_bool(observation.get("disposition_visible"))
    approved = _as_bool(observation.get("approved_checked"))
    conditional = _as_bool(observation.get("conditional_checked"))
    rejected = _as_bool(observation.get("rejected_checked"))
    if visible is None or approved is None or conditional is None or rejected is None:
        return "NG", _UNVERIFIABLE
    if not visible:
        return "NG", _MISSING_FIELD_NG

    checked = [name for name, selected in (("approved", approved), ("conditional", conditional), ("rejected", rejected)) if selected]
    if len(checked) != 1:
        return "NG", _SELECTION_NG
    if checked[0] != "conditional":
        return "PASS", ""

    if rule_no == 9:
        if "approval_date" not in observation:
            return "NG", _UNVERIFIABLE
        approval_date = _parse_approval_date(observation.get("approval_date"))
        if approval_date is None:
            return "NG", _DATE_MISSING_NG
        days_delta = (today - approval_date).days
        if 0 <= days_delta <= _CONDITIONAL_APPROVAL_WINDOW_DAYS:
            return "PASS", ""
        return "NG", _DATE_WINDOW_NG

    comments_present = _as_bool(observation.get("comments_present"))
    if comments_present is None:
        return "NG", _UNVERIFIABLE
    if comments_present:
        return "PASS", ""
    return "NG", _COMMENTS_NG


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

    @staticmethod
    def _load_json_object(response: str) -> dict[str, object]:
        result = json.loads(response.replace("```json", "").replace("```", "").strip())
        if not isinstance(result, dict):
            raise ValueError("visual response must be a JSON object")
        return result

    def _unverifiable(self, config: dict[str, str]) -> AuditResult:
        return AuditResult(
            classification=config["classification"],
            item=config["item"],
            priority="P0",
            status="NG",
            suggestions=_UNVERIFIABLE,
        )

    def evaluate(
        self,
        rule_no: int,
        images: Sequence[str],
        *,
        source_locations: Sequence[str] = (),
        today: date | None = None,
    ) -> AuditResult:
        config = PSW_RULE_PROMPTS[rule_no]
        source_note = ""
        if source_locations:
            source_note = "\n\n视觉证据来源：\n- " + "\n- ".join(source_locations)
        if rule_no in _CONDITIONAL_VISUAL_RULES:
            return self._evaluate_conditional_rule(rule_no, config, list(images), source_note, today or date.today())
        return self._evaluate_status_rule(rule_no, config, list(images), source_note)

    def _evaluate_conditional_rule(
        self,
        rule_no: int,
        config: dict[str, str],
        images: list[str],
        source_note: str,
        today: date,
    ) -> AuditResult:
        user_prompt = (
            f"{config['user_prompt']}{source_note}\n\n"
            "请只返回以下 JSON，不要添加解释：\n"
            "{\n"
            '    "disposition_visible": true,\n'
            '    "approved_checked": false,\n'
            '    "conditional_checked": false,\n'
            '    "rejected_checked": false,\n'
            '    "approval_date": "",\n'
            '    "comments_present": false\n'
            "}\n"
            "空框为 false。看不清“认可情况”时 disposition_visible 为 false。"
            "approval_date 使用 YYYY-MM-DD；没有日期时为空字符串。"
            "补充评论有非空内容时 comments_present 为 true。"
        )
        response = self._analyze(
            system_prompt=config["system_prompt"],
            user_prompt=user_prompt,
            images=images,
            json_format=True,
        )
        try:
            observation = self._load_json_object(response)
            status, suggestions = decide_conditional_visual_rule(rule_no, observation, today=today)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("PSW rule %s visual evidence could not be verified: %s", rule_no, exc)
            return self._unverifiable(config)
        if not suggestions.strip():
            suggestions = self._default_suggestions(rule_no, status)
        return AuditResult(
            classification=config["classification"],
            item=config["item"],
            priority="P0",
            status=status,
            suggestions=suggestions,
        )

    def _evaluate_status_rule(
        self,
        rule_no: int,
        config: dict[str, str],
        images: list[str],
        source_note: str,
    ) -> AuditResult:
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
            images=images,
            json_format=True,
        )
        try:
            result_dict = self._load_json_object(response)
            status = str(result_dict.get("status", "") or "").upper()
            if status not in {"PASS", "NG"}:
                result_dict["status"] = "NG"
                result_dict["suggestions"] = _UNVERIFIABLE
            else:
                result_dict["status"] = status
            if not str(result_dict.get("suggestions", "") or "").strip():
                result_dict["suggestions"] = self._default_suggestions(rule_no, result_dict.get("status"))
            return AuditResult(
                classification=str(result_dict["classification"]),
                item=str(result_dict["item"]),
                priority=str(result_dict["priority"]),
                status=str(result_dict["status"]),
                suggestions=str(result_dict["suggestions"]),
            )
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            logger.warning("PSW rule %s visual evidence could not be verified: %s", rule_no, exc)
            return self._unverifiable(config)


__all__ = [
    "PSW_ALWAYS_VISUAL_RULES",
    "PSW_CHECKBOX_DEPENDENT_RULES",
    "PSW_RULE_PROMPTS",
    "PswVisualAnalyzer",
    "PswVisualRuleEvaluator",
    "decide_conditional_visual_rule",
]

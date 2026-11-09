"""Deterministic PSW policy over source-located facts; no model verdicts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime

from audit.contracts import AuditResultItem as AuditResult

PSW_ALWAYS_VISUAL_RULES = frozenset({3, 5})
PSW_CHECKBOX_DEPENDENT_RULES = frozenset({7, 8, 9, 10})
_DISPOSITION_VISUAL_RULES = frozenset({8, 9, 10})
_SUBMISSION_CHECKBOX_FIELDS = (
    "dimensional_checked",
    "appearance_checked",
    "material_functional_checked",
    "statistical_checked",
    "requirements_yes_checked",
)
_CONDITIONAL_APPROVAL_WINDOW_DAYS = 92
_UNDECIDABLE = "原件证据不足，无法判断所需事实；请核对原件。"
_SELECTION_NG = "“认可情况”需且仅需勾选一个选项。"
_MISSING_FIELD_NG = "未找到“认可情况”字段。"
_DATE_MISSING_NG = "无法识别条件批准日期。"
_DATE_WINDOW_NG = "条件批准日期超出3个月。"
_COMMENTS_NG = "条件批准需填写补充评论。"
_SUBMISSION_NG = "“批准”状态下提交结果勾选不完整。"

PSW_RULE_LABELS = {
    3: "印章信息检查",
    5: "经授权签字和盖章检查",
    7: "提交原因检查",
    8: "认可情况检查",
    9: "条件批准日期检查",
    10: "条件批准补充评论检查",
}
PSW_RULE_FIELDS: dict[int, dict[str, str]] = {
    3: {
        "psw_stamp_present": "保证书实际盖章",
        "psw_stamp_ending_text": (
            "查看实际印章全部区域，按外圈公司名、内部及底部用途文字的阅读顺序，只返回末端2至6个中文汉字；"
            "注册编号中的数字、英文字母和标点不算中文字。若还有质量部、专用章等用途文字，必须取用途文字末端，不能只取外圈公司名。"
            "末端或顺序不明为null；无章为空字符串。"
        ),
        "psw_stamp_ends_with_company": "保证书实际印章完整文字的最后两字为公司，不能只取外圈公司名",
        "authorization_table_present": (
            "存在实际的部门章授权声明表格或授权正文（说明授权主体、部门和用章授权）；交付物清单、目录、附件列表中仅提到该文件名不算授权表。页面只有清单或保证书而没有实际授权表时为false。"
        ),
        "authorization_company_stamp_present": "实际部门章授权声明中盖有公司章；页面没有实际授权声明时为false。部门章/专用章不是公司章，空白公司章位置或标签不算，无法确认返回null。",
    },
    5: {
        "authorized_signature_present": "仅检查经授权的供方代表签字行是否有实际签字内容；代表印刷体姓名/Print Name、职务及客户签字均不能代替该签字行。清晰可见的空白签字行必须为false。",
        "psw_stamp_present": "保证书实际盖章",
    },
    7: {"submission_any_checked": "提交原因至少一个选项实际被选中", "submission_other_present": "提交原因其他说明有内容"},
    8: {
        "disposition_visible": "存在认可情况字段",
        "approved_checked": "认可情况批准实际被选中",
        "conditional_checked": "认可情况条件批准实际被选中",
        "rejected_checked": "认可情况拒绝实际被选中",
        "dimensional_checked": "提交结果尺寸测量实际被选中",
        "appearance_checked": "提交结果外观标准实际被选中",
        "material_functional_checked": "提交结果材料和功能试验实际被选中",
        "statistical_checked": "提交结果统计过程数据实际被选中",
        "requirements_yes_checked": "这些结果均满足所有图纸和规范要求的是实际被选中",
    },
    9: {
        "approval_date": "条件批准的日期（YYYY-MM-DD），明确空白为空字符串",
        "disposition_visible": "存在认可情况字段",
        "approved_checked": "认可情况批准实际被选中",
        "conditional_checked": "认可情况条件批准实际被选中",
        "rejected_checked": "认可情况拒绝实际被选中",
    },
    10: {
        "disposition_visible": "存在认可情况字段",
        "approved_checked": "认可情况批准实际被选中",
        "conditional_checked": "认可情况条件批准实际被选中",
        "rejected_checked": "认可情况拒绝实际被选中",
        "comments_present": "条件批准补充评论有内容",
    },
}


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


def decide_disposition_visual_rule(
    rule_no: int,
    observation: Mapping[str, object],
    *,
    today: date,
) -> tuple[str, str]:
    """Apply the structured PSW rule 8/9/10 order to reported checkbox facts.

    The source does not choose PASS or NG. Missing or unreadable facts are UNABLE.
    """

    if rule_no not in _DISPOSITION_VISUAL_RULES:
        raise ValueError(f"rule {rule_no} is not a disposition visual rule")

    visible = _as_bool(observation.get("disposition_visible"))
    approved = _as_bool(observation.get("approved_checked"))
    conditional = _as_bool(observation.get("conditional_checked"))
    rejected = _as_bool(observation.get("rejected_checked"))
    if visible is None:
        return "UNABLE", _UNDECIDABLE
    if not visible:
        return "NG", _MISSING_FIELD_NG

    checked = [name for name, selected in (("approved", approved), ("conditional", conditional), ("rejected", rejected)) if selected]
    if len(checked) > 1:
        return "NG", _SELECTION_NG
    if approved is None or conditional is None or rejected is None:
        return "UNABLE", _UNDECIDABLE
    if len(checked) != 1:
        return "NG", _SELECTION_NG

    if rule_no == 8:
        if not approved:
            return "PASS", ""
        submission = [_as_bool(observation.get(field)) for field in _SUBMISSION_CHECKBOX_FIELDS]
        if any(value is False for value in submission):
            return "NG", _SUBMISSION_NG
        if any(value is None for value in submission):
            return "UNABLE", _UNDECIDABLE
        return "PASS", ""

    if checked[0] != "conditional":
        return "PASS", ""

    if rule_no == 9:
        if observation.get("approval_date") is None:
            return "UNABLE", _UNDECIDABLE
        approval_date = _parse_approval_date(observation.get("approval_date"))
        if approval_date is None:
            return "NG", _DATE_MISSING_NG
        days_delta = (today - approval_date).days
        if 0 <= days_delta <= _CONDITIONAL_APPROVAL_WINDOW_DAYS:
            return "PASS", ""
        return "NG", _DATE_WINDOW_NG

    comments_present = _as_bool(observation.get("comments_present"))
    if comments_present is None:
        return "UNABLE", _UNDECIDABLE
    if comments_present:
        return "PASS", ""
    return "NG", _COMMENTS_NG


def normalize_psw_stamp_facts(observation: Mapping[str, object]) -> dict[str, object]:
    """Keep inconsistent stamp observations unknown; use canonical source text."""
    facts = dict(observation)
    ending_text = facts.get("psw_stamp_ending_text")
    if not isinstance(ending_text, str):
        if facts.get("psw_stamp_present") is False:
            facts["psw_stamp_ends_with_company"] = False
        elif "psw_stamp_ending_text" in facts:
            facts["psw_stamp_ends_with_company"] = None
        return facts
    text = re.sub(r"[\s，。；、,.;:：()（）]", "", ending_text)
    present = _as_bool(facts.get("psw_stamp_present"))
    if (present is True and not text) or (present is False and text):
        facts.update(psw_stamp_present=None, psw_stamp_ending_text=None, psw_stamp_ends_with_company=None)
    else:
        facts["psw_stamp_ends_with_company"] = text.endswith("公司")
    return facts


def evaluate_psw_observation(rule_no: int, observation: Mapping[str, object], *, today: date | None = None) -> AuditResult:
    """Apply frozen PSW requirements only to established facts.

    Nearby table text alone does not prove checkbox selection, handwriting,
    the terminal seal wording, or a negative seal detection.
    """
    observation = normalize_psw_stamp_facts(observation)
    status, suggestions = "UNABLE", _UNDECIDABLE
    if rule_no in _DISPOSITION_VISUAL_RULES:
        status, suggestions = decide_disposition_visual_rule(rule_no, observation, today=today or date.today())
    elif rule_no == 3:
        company = _as_bool(observation.get("psw_stamp_ends_with_company"))
        authorization = _as_bool(observation.get("authorization_table_present"))
        authorization_stamp = _as_bool(observation.get("authorization_company_stamp_present"))
        if company is True or (authorization is True and authorization_stamp is True):
            status, suggestions = "PASS", ""
        elif company is False and (authorization is False or authorization_stamp is False):
            status, suggestions = "NG", "保证书印章文字未以“公司”结尾，且没有盖有公司章的部门章授权声明。"
        elif company is False:
            suggestions = "原件证据不足：保证书印章不满足公司章要求，但无法确认是否存在盖有公司章的部门章授权声明。请人工核对授权页；无法辨认时保留无法判断。"
    elif rule_no == 5:
        signature = _as_bool(observation.get("authorized_signature_present"))
        stamp = _as_bool(observation.get("psw_stamp_present"))
        if signature is False or stamp is False:
            missing = [label for label, value in (("经授权的供方代表签字", signature), ("盖章", stamp)) if value is False]
            status, suggestions = "NG", "缺少" + "、".join(missing) + "。"
        elif signature is True and stamp is True:
            status, suggestions = "PASS", "经授权的供方代表签字和盖章均已提供。"
    elif rule_no == 7:
        selected = _as_bool(observation.get("submission_any_checked"))
        other = _as_bool(observation.get("submission_other_present"))
        if selected is True or other is True:
            status, suggestions = "PASS", ""
        elif selected is False and other is False:
            status, suggestions = "NG", "提交原因没有选中项且其他说明为空。"
    else:
        raise ValueError(f"unsupported PSW rule {rule_no}")
    if not suggestions:
        suggestions = f"规则{rule_no}检查通过" if status == "PASS" else f"规则{rule_no}检查不通过"
    # This function evaluated one named, deterministic rule over established
    # source facts. Unknown observations do not claim a completed assertion.
    return AuditResult(classification=PSW_RULE_LABELS[rule_no], item=PSW_RULE_LABELS[rule_no], priority="P0", status=status, suggestions=suggestions, checks_performed=int(status in {"PASS", "NG"}))


__all__ = [
    "PSW_ALWAYS_VISUAL_RULES",
    "PSW_CHECKBOX_DEPENDENT_RULES",
    "PSW_RULE_FIELDS",
    "PSW_RULE_LABELS",
    "decide_disposition_visual_rule",
    "evaluate_psw_observation",
]

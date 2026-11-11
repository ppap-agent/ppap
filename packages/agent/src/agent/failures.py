"""Safe, actionable product messages; never publish model or subprocess exceptions."""

MESSAGES = {
    "model_unavailable": ("模型暂不可用，或企业的模型配置在受理后已变化。本轮未开始，请重新发起。", False),
    "deep_runtime_unavailable": ("深度审核服务暂不可用，请稍后重新提交。", True),
    "deep_lease_lost": ("本次执行已停止，未发布未经确认的结果。", False),
    "deep_cancelled": ("本次执行已停止。", False),
    "deep_budget_exhausted": ("本轮已达到执行上限，尚未完成审核。请联系管理员调整上限后，使用相同资料重审。", False),
    "deep_timeout": ("本轮深度审核超时，未完成的检查没有判为通过。", False),
    "deep_input_missing": ("无法读取本轮资料，请检查文件是否可用。", False),
    "deep_input_invalid": ("本轮资料或规则不完整，请返回审核准备核对。", False),
    "deep_input_unauthorized": ("本轮资料授权不一致，未执行审核。", False),
    "deep_integrity_mismatch": ("本轮资料与所选版本不一致，请重新提交。", False),
    "deep_rules_empty": ("规则中没有可审核的条款，请检查并发布规则正文。", False),
    "deep_parser_limit": ("部分资料超出当前可读取范围，请核对格式或拆分资料后重审。", False),
    "deep_model_protocol": ("模型没有完成有效的审核输出，本轮未发布结论。", False),
    "deep_output_invalid": ("审核输出未通过规则覆盖或证据校验，本轮未发布结论。", False),
    "deep_output_identity_mismatch": ("审核结果与本轮资料版本不符，未发布结论。", False),
    "executor_revision_mismatch": ("本轮执行版本已不可用，请基于相同资料新建审核。", False),
    "deep_cleanup_failed": ("执行环境收尾失败，结果尚未发布，请联系管理员。", False),
}


def failure_result(code: str) -> dict:
    message, retryable = MESSAGES.get(code, ("深度审核未完成，本轮未发布结论。请检查执行详情后重审。", False))
    return {
        "note": code if code in MESSAGES else "deep_execution_failed",
        "findings": [],
        "coverage": {"checked": [], "unchecked": [], "locator_gap": False, "rule_items": []},
        "failure": {"code": code if code in MESSAGES else "deep_execution_failed", "message": message, "retryable": retryable},
    }

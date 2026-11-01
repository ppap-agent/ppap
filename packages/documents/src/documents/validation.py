import re
from typing import Any, Dict, List
from urllib.parse import urlparse


def validate_url(url: str) -> bool:
    """验证URL格式"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False


def validate_rule_id(rule_id: str) -> bool:
    """验证规则ID格式"""
    if not rule_id or len(rule_id) < 3 or len(rule_id) > 255:
        return False

    # 只允许字母、数字、下划线和连字符
    pattern = r"^[a-zA-Z0-9_-]+$"
    return bool(re.match(pattern, rule_id))


def validate_audit_id(audit_id: str) -> bool:
    """验证审核ID格式"""
    if not audit_id or len(audit_id) < 3 or len(audit_id) > 255:
        return False

    # 只允许字母、数字、下划线和连字符
    pattern = r"^[a-zA-Z0-9_-]+$"
    return bool(re.match(pattern, audit_id))


def validate_task_id(task_id: str) -> bool:
    """验证任务ID格式"""
    if not task_id or len(task_id) < 3 or len(task_id) > 255:
        return False

    # 只允许字母、数字、下划线和连字符
    pattern = r"^[a-zA-Z0-9_-]+$"
    return bool(re.match(pattern, task_id))


def validate_file_size(file_size: int, max_size: int) -> bool:
    """验证文件大小"""
    return 0 < file_size <= max_size


def validate_file_extension(extension: str, allowed_extensions: List[str]) -> bool:
    """验证文件扩展名"""
    return extension.lower() in [ext.lower() for ext in allowed_extensions]


def validate_customer_name(customer: str) -> bool:
    """验证客户名称"""
    if not customer or len(customer) < 1 or len(customer) > 100:
        return False

    # 允许中文、英文、数字和常见符号
    pattern = r"^[\u4e00-\u9fa5a-zA-Z0-9\s\-_()（）]+$"
    return bool(re.match(pattern, customer))


def validate_model_name(model: str) -> bool:
    """验证车型名称"""
    if not model or len(model) < 1 or len(model) > 100:
        return False

    # 允许字母、数字和常见符号
    pattern = r"^[a-zA-Z0-9\s\-_()（）]+$"
    return bool(re.match(pattern, model))


def validate_part_name(part: str) -> bool:
    """验证零件名称"""
    if not part or len(part) < 1 or len(part) > 200:
        return False

    # 允许中文、英文、数字和常见符号
    pattern = r"^[\u4e00-\u9fa5a-zA-Z0-9\s\-_()（）]+$"
    return bool(re.match(pattern, part))


def validate_priority(priority: str) -> bool:
    """验证优先级"""
    valid_priorities = ["P0", "P1", "P2", "P3", "P4", "P5"]
    return priority in valid_priorities


def validate_audit_status(status: str) -> bool:
    """验证审核状态"""
    valid_statuses = ["PASS", "NG", "ERR"]
    return status in valid_statuses


def validate_task_status(status: str) -> bool:
    """验证任务状态"""
    valid_statuses = ["pending", "running", "completed", "failed"]
    return status in valid_statuses


def validate_batch_request(requests: List[Dict[str, Any]], max_batch_size: int = 100) -> List[str]:
    """验证批处理请求"""
    errors = []

    if not requests:
        errors.append("请求列表不能为空")
        return errors

    if len(requests) > max_batch_size:
        errors.append(f"批处理请求数量不能超过{max_batch_size}个")

    return errors


def sanitize_input(input_str: str) -> str:
    """清理输入字符串"""
    if not input_str:
        return ""

    # 移除首尾空白
    input_str = input_str.strip()

    # 移除潜在的恶意字符
    dangerous_chars = ["<", ">", '"', "'", "&", ";", "|", "`"]
    for char in dangerous_chars:
        input_str = input_str.replace(char, "")

    return input_str


def validate_json_structure(data: Dict[str, Any], required_fields: List[str]) -> List[str]:
    """验证JSON结构"""
    errors = []

    for field in required_fields:
        if field not in data:
            errors.append(f"缺少必需字段: {field}")
        elif data[field] is None:
            errors.append(f"字段不能为空: {field}")

    return errors

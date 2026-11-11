"""Shared constants/helpers for PfmeaAgent."""

from typing import Dict, List

from audit.checker_lib.li.cross_file_extractors import normalize_key

PFD_FIELD_ALIASES: Dict[str, List[str]] = {
    "operation_no": ["工序号", "工序编号", "operation no", "process no"],
    "operation_name": [
        "工序名称",
        "工序内容",
        "过程名称",
        "operation name",
        "process name",
    ],
    "product_special_char": ["产品特殊特性", "product special characteristic"],
    "product_char_type": ["产品特性类别", "产品特性"],
    "process_special_char": ["过程特殊特性", "过程控制特性", "process special characteristic"],
    "process_char_type": ["过程特性类别", "过程特性"],
}

PFMEA_SELF_FIELD_ALIASES: Dict[str, List[str]] = {
    "process_step": [
        "过程步骤",
        "过程项目",
        "过程项功能",
        "Process Step",
        "Process Item",
    ],
    "special_char": ["特殊特性", "Special Characteristic", "特殊特性分类"],
}


def value_matches(left: str, right: str) -> bool:
    left_key = normalize_key(left)
    right_key = normalize_key(right)
    if not left_key or not right_key:
        return False
    return left_key == right_key or left_key in right_key or right_key in left_key


def display_values(values: List[str], max_items: int = 5) -> str:
    picked = [value for value in values if value][:max_items]
    return ", ".join(picked) if picked else "无"

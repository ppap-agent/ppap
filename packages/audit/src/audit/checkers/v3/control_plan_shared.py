"""Shared constants/helpers for ControlPlanAgent."""

from typing import Dict, List

from audit.checkers.v3.cross_file_extractors import (
    build_struct_key,
    normalize_key,
)

PROD_OK = {"生产", "SOP", "Production"}
PROD_NG = {"样件", "试生产", "PP", "OTS", "Prototype", "Trial"}

SPECIAL_ALLOWED = {
    "关键特殊特性",
    "重要特殊特性",
    "NA",
    "[G]",
    "[Z]",
    "G",
    "Z",
    "[CC]",
    "[SC]",
    "CC",
    "SC",
}

SAMPLE_COL_KWS = ["样本容量", "样本量", "数量", "样本", "Sample Size", "Sample", "Size"]
SPECIAL_COL_KWS = ["特殊特性分类", "特殊特性", "特性分类", "Special", "Char", "Class"]
SPECIAL_STRICT_KWS = ["特殊特性分类", "特殊特性", "特性分类"]
SPECIAL_ENGLISH_KWS = [
    "Special.*Char.*Class",
    "Special.*Character.*Class",
    "Special.*Characteristic.*Class",
]
FREQ_COL_KWS = ["频率", "频次", "Frequency", "Freq"]
MEASURE_COL_KWS = ["评价测量技术", "测量技术"]

HEADER_SCAN_ROWS = 20
HEADER_SCAN_COLS = 30
DATA_PREVIEW_ROWS = 200

CP_FIELD_ALIASES: Dict[str, List[str]] = {
    "operation_no": [
        "工序号",
        "工序编号",
        "零件/过程号码",
        "零件/工序号",
        "零件/过程编号",
        "operation no",
        "process no",
        "Part / Process Number",
        "Part/Process Number",
    ],
    "operation_name": [
        "工序名称",
        "工序内容",
        "过程名称",
        "工序名称/操作描述",
        "过程名称/操作描述",
        "operation name",
        "process name",
        "Process Name / Operation Description",
    ],
    "special_characteristic": [
        "特殊特性分类",
        "特殊特性",
        "special characteristic",
        "Special Char. Class",
    ],
    "control_method": ["控制方法", "control method", "管控方法", "Control Method"],
    "sample_size": ["样本容量", "样本量", "数量", "sample size", "sample", "Size"],
    "frequency": ["频率", "频次", "frequency", "freq", "Freq."],
    "measurement_technique": [
        "评价测量技术",
        "测量技术",
        "measurement technique",
        "Evaluation / Measurement Technique",
    ],
    "product": ["产品", "Product"],
    "process": ["过程", "Process"],
    "spec_tolerance": [
        "产品/工序规范/公差",
        "产品/过程规范/公差",
        "规范/公差",
        "Product / Process Specification",
        "Specification/Tolerance",
    ],
}

PFD_FIELD_ALIASES: Dict[str, List[str]] = {
    "operation_no": ["工序号", "工序编号", "operation no", "process no"],
    "operation_name": [
        "工序名称",
        "工序内容",
        "过程名称",
        "operation name",
        "process name",
    ],
}

SPECIAL_FIELD_ALIASES: Dict[str, List[str]] = {
    "operation_no": ["工序号", "工序编号", "operation no", "process no"],
    "operation_name": [
        "工序名称",
        "工序内容",
        "过程名称",
        "operation name",
        "process name",
    ],
    "characteristic": ["特殊特性", "特性", "special characteristic", "characteristic"],
    "control_method": ["控制方法", "control method", "管控方法", "制造过程"],
    "sample_size": ["样本容量", "样本量", "数量", "sample size", "sample", "样本数"],
    "frequency": ["频率", "频次", "frequency", "freq"],
}

SCL_FIELD_ALIASES_V2: Dict[str, List[str]] = {
    "char_class": ["特性分类"],
    "char_description": ["特性描述"],
    "char_value": ["特性值"],
    "special_type": ["特殊特性种类", "特殊特性类别"],
    "control_method": ["控制方法", "制造过程", "控制方法（制造过程）"],
    "sample_size": ["样本数", "样本量", "样本容量"],
    "frequency": ["频次", "频率"],
}

PFMEA_FIELD_ALIASES: Dict[str, List[str]] = {
    "process_step": ["过程步骤", "过程项目", "Process Step", "Process Item"],
    "operation_no": ["工序号", "工序编号", "operation no", "process no"],
    "operation_name": [
        "工序名称",
        "工序内容",
        "过程名称",
        "operation name",
        "process name",
    ],
    "special_char": ["特殊特性", "Special Characteristic", "特性"],
    "characteristic": ["过程特性", "产品特性", "特性", "characteristic"],
    "prevention_control": [
        "现行预防控制",
        "预防控制",
        "控制预防",
        "预防措施",
        "Prevention Control",
        "prevention",
        "preventive",
    ],
    "detection_control": [
        "现行探测控制",
        "探测控制",
        "控制探测",
        "探测措施",
        "Detection Control",
        "detection",
        "detective",
    ],
}


def extract_operation_keys(rows: List[Dict[str, str]], include_name: bool) -> set[str]:
    keys: set[str] = set()
    for row in rows:
        operation_no = row.get("operation_no", "")
        operation_name = row.get("operation_name", "")
        key = build_struct_key(operation_no, operation_name) if include_name else normalize_key(operation_no)
        if key:
            keys.add(key)
    return keys


def extract_characteristic_keys(rows: List[Dict[str, str]], field_name: str) -> set[str]:
    keys: set[str] = set()
    for row in rows:
        key = build_struct_key(
            row.get("operation_no", ""),
            row.get("operation_name", ""),
            row.get(field_name, ""),
        )
        if key:
            keys.add(key)
    return keys


def value_matches(left: str, right: str) -> bool:
    left_key = normalize_key(left)
    right_key = normalize_key(right)
    if not left_key or not right_key:
        return False
    return left_key == right_key or left_key in right_key or right_key in left_key


def display_values(values: List[str], max_items: int = 5) -> str:
    picked = [value for value in values if value][:max_items]
    return ", ".join(picked) if picked else "无"

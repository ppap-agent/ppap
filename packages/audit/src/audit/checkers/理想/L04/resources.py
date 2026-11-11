"""Clause readers and explicit unsupported scopes owned by this leaf."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L04",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf"),
    "clauses": {
        "L04:1": {"method": "rule_1", "scope": "唯一材料业务表；按零件号逐行核对原始XLSX固定数值格式或数字文本的四位小数；General数值、公式及复杂格式保持未知。", "carriers": (".xlsx", ".xlsm")},
        "L04:2": {"method": "rule_2", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L04:3": {"method": "rule_3", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L04:4": {"method": "rule_4", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
    },
    "known_gaps": {},
    "limitations": (
        "原生PDF表格仅在现有PDF→Excel成功且保留完整原件位置时检查；未识别版式及扫描内容保持UNABLE。",
        "图形事实须在原图中定位；原生空框、文字标签和图像对象数量不能认证实际勾选、签字或章文。",
    ),
}

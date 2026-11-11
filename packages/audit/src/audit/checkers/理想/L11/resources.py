"""Clause readers and explicit unsupported scopes owned by this leaf."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L11",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf"),
    "clauses": {
        "L11:1": {"method": "rule_1", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L11:4": {"method": "rule_4", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L11:5": {"method": "rule_5", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L11:6": {"method": "rule_6", "scope": "已定位原图区域的具体图形事实；多页/多表关系不能完整建立时保持未知；不支持DOCX图形读取。", "carriers": (".xlsx", ".xlsm", ".xls", ".pdf")},
    },
    "known_gaps": {"L11:2": "两个同名特性类别列的产品/过程归属尚未迁入，不能任取第一列。", "L11:3": "两个同名特性类别列的产品/过程归属尚未迁入，不能任取第二个未知归属列。"},
    "limitations": (
        "原生PDF表格仅在现有PDF→Excel成功且保留完整原件位置时检查；未识别版式及扫描内容保持UNABLE。",
        "图形事实须在原图中定位；原生空框、文字标签和图像对象数量不能认证实际勾选、签字或章文。",
    ),
}

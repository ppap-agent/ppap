"""Clause readers and explicit unsupported scopes owned by this leaf."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L13",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf"),
    "clauses": {
        "L13:1": {"method": "rule_1", "scope": "单行唯一表头且设备分组列明确位于子列之前；多层合并/重复组名尚不能判定。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L13:2": {"method": "rule_2", "scope": "单行唯一表头且设备分组列明确位于子列之前；多层合并/重复组名尚不能判定。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
    },
    "known_gaps": {},
    "limitations": (
        "原生PDF表格仅在现有PDF→Excel成功且保留完整原件位置时检查；未识别版式及扫描内容保持UNABLE。",
        "图形事实须在原图中定位；原生空框、文字标签和图像对象数量不能认证实际勾选、签字或章文。",
    ),
}

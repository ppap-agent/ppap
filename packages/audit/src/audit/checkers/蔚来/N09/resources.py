"""Clause readers and explicit unsupported scopes owned by this leaf."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N09",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf"),
    "clauses": {
        "N09:1": {"method": "rule_1", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "N09:2": {"method": "rule_2", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "N09:3": {
            "method": "rule_3",
            "scope": "原生Excel实际R&R报告，样本编号C7:L7为1至10、评价人A/B/C及三次试验匹配后核对C8:L10、C13:L15、C18:L20；参考/指南不当作数据，合并/公式未知不判通过。",
            "carriers": (".xlsx", ".xlsm"),
        },
        "N09:4": {"method": "rule_4", "scope": "已定位原图区域的具体图形事实；多页/多表关系不能完整建立时保持未知；不支持DOCX图形读取。", "carriers": (".xlsx", ".xlsm", ".xls", ".pdf")},
    },
    "known_gaps": {},
    "limitations": (
        "原生PDF表格仅在现有PDF→Excel成功且保留完整原件位置时检查；未识别版式及扫描内容保持UNABLE。",
        "图形事实须在原图中定位；原生空框、文字标签和图像对象数量不能认证实际勾选、签字或章文。",
    ),
}

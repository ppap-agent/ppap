"""Clause readers and explicit unsupported scopes owned by this leaf."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L01",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf"),
    "clauses": {
        "L01:1": {"method": "rule_1", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L01:2": {"method": "rule_2", "scope": "已定位原图区域的具体图形事实；多页/多表关系不能完整建立时保持未知；不支持DOCX图形读取。", "carriers": (".xlsx", ".xlsm", ".xls", ".pdf")},
        "L01:4": {"method": "rule_4", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L01:8": {"method": "rule_8", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
    },
    "known_gaps": {
        "L01:3": "尺寸/非尺寸分区及多样本公差归属尚未迁入，不用全表数字代替区域读取。",
        "L01:5": "尺寸区同行多样本边界尚未迁入，不能按部分样本判断重复值。",
        "L01:6": "非尺寸区域的完整边界尚未迁入，不能将尺寸行或页尾当作非尺寸结论。",
        "L01:7": "尺寸区OK值与同行指定工具的完整关联尚未迁入。",
    },
    "limitations": (
        "原生PDF表格仅在现有PDF→Excel成功且保留完整原件位置时检查；未识别版式及扫描内容保持UNABLE。",
        "图形事实须在原图中定位；原生空框、文字标签和图像对象数量不能认证实际勾选、签字或章文。",
    ),
}

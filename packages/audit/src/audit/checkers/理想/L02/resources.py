"""Clause readers and explicit unsupported scopes owned by this leaf."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L02",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf"),
    "clauses": {
        "L02:1": {"method": "rule_1", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L02:4": {"method": "rule_4", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L02:5": {"method": "rule_5", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
    },
    "known_gaps": {
        "L02:2": "试验样本描述/报告栏等多级分组表头的完整必填范围尚未迁入。",
        "L02:3": "状态列必须归属试验样本描述，当前尚未支持该多级表头归属。",
        "L02:6": "实际试验结果必须位于试验报告栏，当前尚未支持该多级表头归属。",
        "L02:7": "试验报告编号必须位于试验报告栏，当前尚未支持该多级表头归属。",
    },
    "limitations": (
        "原生PDF表格仅在现有PDF→Excel成功且保留完整原件位置时检查；未识别版式及扫描内容保持UNABLE。",
        "图形事实须在原图中定位；原生空框、文字标签和图像对象数量不能认证实际勾选、签字或章文。",
    ),
}

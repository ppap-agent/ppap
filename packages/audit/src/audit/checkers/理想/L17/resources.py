"""Clause readers and explicit unsupported scopes owned by this leaf."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L17",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf"),
    "clauses": {
        "L17:2": {"method": "rule_2", "scope": "唯一正式PSW重量字段；原始XLSX固定格式或数字文本的四位小数；排除指南，General数值、公式及复杂格式保持未知。", "carriers": (".xlsx", ".xlsm")},
        "L17:1": {"method": "rule_1", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L17:3": {"method": "rule_3", "scope": "已定位原图区域的具体图形事实；多页/多表关系不能完整建立时保持未知；不支持DOCX图形读取。", "carriers": (".xlsx", ".xlsm", ".xls", ".pdf")},
        "L17:5": {"method": "rule_5", "scope": "已定位原图区域的具体图形事实；多页/多表关系不能完整建立时保持未知；不支持DOCX图形读取。", "carriers": (".xlsx", ".xlsm", ".xls", ".pdf")},
        "L17:6": {"method": "rule_6", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L17:7": {"method": "rule_7", "scope": "原生字段或唯一单行表头；公式、歧义和未完整读取的范围保持未知。", "carriers": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf")},
        "L17:8": {"method": "rule_8", "scope": "已定位原图区域的具体图形事实；多页/多表关系不能完整建立时保持未知；不支持DOCX图形读取。", "carriers": (".xlsx", ".xlsm", ".xls", ".pdf")},
    },
    "known_gaps": {
        "L17:4": "供应商总公司身份和名称/代码的原件结构确认尚未迁入。",
        "L17:9": "条件批准的唯一实际选择、右下角日期定位与三日历月时效尚未联合迁入。",
        "L17:10": "条件批准的唯一实际选择与补充评论字段归属尚未联合迁入。",
    },
    "limitations": (
        "原生PDF表格仅在现有PDF→Excel成功且保留完整原件位置时检查；未识别版式及扫描内容保持UNABLE。",
        "图形事实须在原图中定位；原生空框、文字标签和图像对象数量不能认证实际勾选、签字或章文。",
    ),
}

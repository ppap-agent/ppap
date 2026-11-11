"""Clause readers and explicit unsupported scopes owned by this leaf."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L09",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".docx", ".pdf"),
    "clauses": {
        "L09:2": {"method": "rule_2", "scope": "原生Excel单表前五行内唯一检具清单标题的车型代码格式；DOCX/PDF标题位置尚未支持。", "carriers": (".xlsx", ".xlsm", ".xls")},
        "L09:3": {"method": "rule_3", "scope": "已定位原图区域的具体图形事实；多页/多表关系不能完整建立时保持未知；不支持DOCX图形读取。", "carriers": (".xlsx", ".xlsm", ".xls", ".pdf")},
    },
    "known_gaps": {"L09:1": "检具清单完整非备注字段与独立认可报告填写内容的联合范围尚未迁入。"},
    "limitations": (
        "原生PDF表格仅在现有PDF→Excel成功且保留完整原件位置时检查；未识别版式及扫描内容保持UNABLE。",
        "图形事实须在原图中定位；原生空框、文字标签和图像对象数量不能认证实际勾选、签字或章文。",
    ),
}

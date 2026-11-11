"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N01",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.dimension_measurement_excel_agent:DimensionMeasurementExcelAgent",
        ".xls": "audit.checker_lib.nio.dimension_measurement_excel_agent:DimensionMeasurementExcelAgent",
        ".xlsm": "audit.checker_lib.nio.dimension_measurement_excel_agent:DimensionMeasurementExcelAgent",
        ".pdf": "audit.checker_lib.nio.dimension_measurement_pdf_agent:DimensionMeasurementPDFAgent",
    },
    "methods": {
        "N01:1": "rule_1",
        "N01:2": "rule_2",
        "N01:3": "rule_3",
        "N01:4": "rule_4",
        "N01:5": "rule_5",
        "N01:6": "rule_6",
        "N01:7": "rule_7",
        "N01:8": "rule_8",
        "N01:9": "rule_9",
        "N01:10": "rule_10",
        "N01:11": "rule_11",
    },
    "implementation_rules": [
        {"id": "1", "category": "CoverSheet页存在性及内容完整性审核", "rule_to_check": "CoverSheet页存在性及内容完整性审核"},
        {"id": "2", "category": "测量报告列存在性及数据非空审核", "rule_to_check": "测量报告列存在性及数据非空审核"},
        {"id": "3", "category": "特定控制方向的公差NA值一致性审核", "rule_to_check": "特定控制方向的公差NA值一致性审核"},
        {"id": "4", "category": "名义值为空时的偏差值存在性审核", "rule_to_check": "名义值为空时的偏差值存在性审核"},
        {"id": "5", "category": "类型列内容合规性审核", "rule_to_check": "类型列内容合规性审核"},
        {"id": "6", "category": "测点号与测点图标记点对应性审核", "rule_to_check": "测点号与测点图标记点对应性审核"},
        {"id": "7", "category": "特定表类型的偏差值在公差范围内审核", "rule_to_check": "特定表类型的偏差值在公差范围内审核"},
        {"id": "8", "category": "中性值（Centering）计算准确性审核", "rule_to_check": "中性值（Centering）计算准确性审核"},
        {"id": "9", "category": "中性值（Centering）阈值判定及预警审核", "rule_to_check": "中性值（Centering）阈值判定及预警审核"},
        {"id": "10", "category": "极差值（Range）计算准确性审核", "rule_to_check": "极差值（Range）计算准确性审核"},
        {"id": "11", "category": "极差值（Range）阈值判定及预警审核", "rule_to_check": "极差值（Range）阈值判定及预警审核"},
    ],
}

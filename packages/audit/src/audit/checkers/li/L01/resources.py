"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L01",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.dimension_measurement_excel_agent:DimensionMeasurementExcelAgent",
        ".xls": "audit.checker_lib.li.dimension_measurement_excel_agent:DimensionMeasurementExcelAgent",
        ".xlsm": "audit.checker_lib.li.dimension_measurement_excel_agent:DimensionMeasurementExcelAgent",
        ".pdf": "audit.checker_lib.li.dimension_measurement_pdf_agent:DimensionMeasurementPDFAgent",
    },
    "methods": {"L01:1": "rule_1", "L01:2": "rule_2", "L01:3": "rule_3", "L01:4": "rule_4", "L01:5": "rule_5", "L01:6": "rule_6", "L01:7": "rule_7", "L01:8": "rule_8"},
    "implementation_rules": [
        {"id": "1", "category": "标题识别检查", "rule_to_check": "标题识别检查"},
        {"id": "2", "category": "重要度标记检查", "rule_to_check": "重要度标记检查"},
        {"id": "3", "category": "尺寸区数值公差带检查", "rule_to_check": "尺寸区数值公差带检查"},
        {"id": "4", "category": "检查方法/工具列检查", "rule_to_check": "检查方法/工具列检查"},
        {"id": "5", "category": "尺寸区数值数据唯一性检查", "rule_to_check": "尺寸区数值数据唯一性检查"},
        {"id": "6", "category": "非尺寸数据检查", "rule_to_check": "非尺寸数据检查"},
        {"id": "7", "category": "尺寸区OK数据方法匹配检查", "rule_to_check": "尺寸区OK数据方法匹配检查"},
        {"id": "8", "category": "关键字段识别检查", "rule_to_check": "关键字段识别检查"},
    ],
}

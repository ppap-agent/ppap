"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "理想",
    "preset_key": "L17",
    "carriers": {
        ".xlsx": "audit.checker_lib.li.psw_excel_agent:PswExcelAgent",
        ".xls": "audit.checker_lib.li.psw_excel_agent:PswExcelAgent",
        ".xlsm": "audit.checker_lib.li.psw_excel_agent:PswExcelAgent",
        ".pdf": "audit.checker_lib.li.psw_pdf_agent:PswPDFAgent",
        ".docx": "audit.checker_lib.li.psw_docx_agent:PswDocxAgent",
    },
    "methods": {
        "L17:1": "rule_1",
        "L17:2": "rule_2",
        "L17:3": "rule_3",
        "L17:4": "rule_4",
        "L17:5": "rule_5",
        "L17:6": "rule_6",
        "L17:7": "rule_7",
        "L17:8": "rule_8",
        "L17:9": "rule_9",
        "L17:10": "rule_10",
    },
    "implementation_rules": [
        {"id": "1", "category": "附件列内容检查", "rule_to_check": "附件列内容检查"},
        {"id": "2", "category": "重量字段检查", "rule_to_check": "重量字段检查"},
        {"id": "3", "category": "印章检查", "rule_to_check": "印章检查"},
        {"id": "4", "category": "供应商名称/供应商代码格式检查", "rule_to_check": "供应商名称/供应商代码格式检查"},
        {"id": "5", "category": "签字和盖章检查", "rule_to_check": "签字和盖章检查"},
        {"id": "6", "category": "必填字段检查", "rule_to_check": "必填字段检查"},
        {"id": "7", "category": "提交原因检查", "rule_to_check": "提交原因检查"},
        {"id": "8", "category": "认可情况检查", "rule_to_check": "认可情况检查"},
        {"id": "9", "category": "条件批准日期检查", "rule_to_check": "条件批准日期检查"},
        {"id": "10", "category": "条件批准补充评论检查", "rule_to_check": "条件批准补充评论检查"},
    ],
}

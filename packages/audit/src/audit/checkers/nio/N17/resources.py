"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N17",
    "carriers": {
        ".xlsx": "audit.checker_lib.nio.psw_excel_agent:PswExcelAgent",
        ".xls": "audit.checker_lib.nio.psw_excel_agent:PswExcelAgent",
        ".xlsm": "audit.checker_lib.nio.psw_excel_agent:PswExcelAgent",
        ".pdf": "audit.checker_lib.nio.psw_pdf_agent:PswPDFAgent",
        ".docx": "audit.checker_lib.nio.psw_docx_agent:PswDocxAgent",
    },
    "methods": {
        "N17:1": "rule_1",
        "N17:2": "rule_2",
        "N17:3": "rule_3",
        "N17:4": "rule_4",
        "N17:5": "rule_5",
        "N17:6": "rule_6",
        "N17:7": "rule_7",
        "N17:8": "rule_8",
        "N17:9": "rule_9",
        "N17:10": "rule_10",
    },
    "implementation_rules": [
        {"id": "1", "category": "生产件批准交付物文件清单附件检查", "rule_to_check": "生产件批准交付物文件清单附件检查"},
        {"id": "2", "category": "重量字段格式检查", "rule_to_check": "重量字段格式检查"},
        {"id": "3", "category": "印章及授权声明检查", "rule_to_check": "印章及授权声明检查"},
        {"id": "4", "category": "供应商名称格式检查", "rule_to_check": "供应商名称格式检查"},
        {"id": "5", "category": "签字与盖章完整性检查", "rule_to_check": "签字与盖章完整性检查"},
        {"id": "6", "category": "关键字段非空检查", "rule_to_check": "关键字段非空检查"},
        {"id": "7", "category": "提交原因选项检查", "rule_to_check": "提交原因选项检查"},
        {"id": "8", "category": "认可情况及结果勾选检查", "rule_to_check": "认可情况及结果勾选检查"},
        {"id": "9", "category": "条件批准日期时效检查", "rule_to_check": "条件批准日期时效检查"},
        {"id": "10", "category": "条件批准补充评论检查", "rule_to_check": "条件批准补充评论检查"},
    ],
}

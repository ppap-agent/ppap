"""Resource aliases and explicit source-clause methods for UAES control plans."""

RESOURCE = {
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".pdf", ".docx"),
    "clauses": {
        "U08:1": {
            "operation_headers": ("工序名称/操作描述", "过程名称/操作描述", "工序名称", "操作描述"),
            "resource_headers": ("机器装置夹具工装", "机器/装置/夹具/工装", "生产资源", "机器设备工装", "生产设备"),
            "method": "rule_1",
        },
        "U08:2": {"method": "rule_2"},
        "U08:3": {"method": "rule_3"},
        "U08:4": {"mark_headers": ("特性类型", "特殊特性"), "method": "rule_4"},
        "U08:6": {"method": "rule_5"},
        "U08:7": {"method": "rule_6"},
        "U08:8": {"method": "rule_7"},
        "U08:9": {"method": "rule_8"},
        "U08:10": {"method": "rule_9"},
        "U08:11": {"method": "rule_10"},
        "U08:12": {"method": "rule_11"},
        "U08:13": {"method": "rule_12"},
    },
    "customer": "UAES",
    "preset_key": "U08",
}

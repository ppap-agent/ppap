"""Actual N05 readers and explicit remaining evidence gaps."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N05",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".pdf"),
    "clauses": {
        "N05:1": {"method": "rule_1"},
        "N05:2": {"method": "rule_2"},
        "N05:3": {"method": "rule_3"},
        "N05:4": {"method": "rule_4"},
        "N05:5": {"method": "rule_5"},
        "N05:6": {"method": "rule_6"},
        "N05:7": {"method": "rule_7"},
        "N05:8": {"method": "rule_8"},
    },
    "known_gaps": {"N05:9": "尚未实现与本轮特殊特性清单的授权配对及逐项特性比对，不能用同名文件或旧版结果替代。"},
}

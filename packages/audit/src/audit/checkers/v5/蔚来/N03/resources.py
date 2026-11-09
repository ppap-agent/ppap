"""N03 local readers and unverified cross-file evidence gaps."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N03",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".pdf"),
    "clauses": {
        "N03:1": {"method": "rule_1"},
        "N03:2": {"method": "rule_2"},
        "N03:3": {"method": "rule_3"},
        "N03:4": {"method": "rule_4"},
        "N03:5": {"method": "rule_5"},
    },
    "known_gaps": {
        "N03:6": "尚未验证与本轮过程流程图的授权原件配对及全部工序身份对应，需人工比对。",
        "N03:7": "尚未验证与本轮PFMEA的授权配对及工序特殊特性逐项对应，需人工比对。",
        "N03:8": "尚未实现本轮特殊特性清单的完整逐项覆盖读取和配对，需人工比对。",
        "N03:9": "尚未验证本轮PFMEA四类措施与控制计划同工序评价测量技术的对应关系。",
        "N03:10": "尚未验证本轮特殊特性清单与控制计划的同项方法、数量和频率配对。",
    },
}

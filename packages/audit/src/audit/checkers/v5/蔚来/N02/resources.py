"""Implemented N02 native readers and remaining semantic/pairing gaps."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N02",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".pdf"),
    "clauses": {"N02:1": {"method": "rule_1"}, "N02:2": {"method": "rule_2"}},
    "known_gaps": {
        "N02:3": "尚未实现过程项功能三个语义维度的有来源分段判断，不能只见三个标签就判通过。",
        "N02:4": "尚未实现失效影响三个独立维度/分值对应及严重度最大值检查。",
        "N02:5": "尚未实现独立探测措施/分值对应及探测度最小值检查。",
        "N02:6": "尚未验证本轮过程流程图授权配对与全部工序对应，需人工比对。",
        "N02:7": "尚未验证本轮过程流程图与PFMEA同工序CC/SC特性的授权配对。",
    },
}

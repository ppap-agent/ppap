"""N01 supported literal/threshold readers; complex sample geometry stays explicit."""

RESOURCE = {
    "customer": "蔚来",
    "preset_key": "N01",
    "supported_suffixes": (".xlsx", ".xlsm", ".xls", ".pdf"),
    "clauses": {
        "N01:1": {"method": "rule_1"},
        "N01:2": {"method": "rule_2"},
        "N01:3": {"method": "rule_3"},
        "N01:5": {"method": "rule_5"},
        "N01:9": {"method": "rule_9"},
        "N01:11": {"method": "rule_11"},
    },
    "known_gaps": {
        "N01:4": "尚未可靠读取多级测量偏差值下的全部样本列及名义值空值关系。",
        "N01:6": "尚未实现测点图/截面原件标记点与全部测点号的视觉关联读取。",
        "N01:7": "尚未可靠读取五类测量表的全部样本列，不能用Part1代表完整公差判定。",
        "N01:8": "尚未实现全部样本列的原生读取与Centering精确公式核对。",
        "N01:10": "尚未实现全部样本列的原生读取与Range精确公式核对。",
    },
    "limitations": ("存在多份业务测量表时不能选择第一份，当前保留UNABLE。",),
}

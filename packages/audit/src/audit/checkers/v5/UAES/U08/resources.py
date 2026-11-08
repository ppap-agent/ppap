"""Header words for the two U08 clauses this leaf can actually read.

Other U08 clauses stay unbound until they have their own evidence checks.
"""

RESOURCE = {
    "supported_suffixes": (".xlsx", ".xlsm"),
    "clauses": {
        "U08:1": {
            "operation_headers": ("工序名称/操作描述", "过程名称/操作描述", "工序名称", "操作描述"),
            "resource_headers": ("机器装置夹具工装", "机器/装置/夹具/工装", "生产资源", "机器设备工装", "生产设备"),
        },
        "U08:4": {
            "mark_headers": ("特性类型", "特殊特性"),
        },
    },
}

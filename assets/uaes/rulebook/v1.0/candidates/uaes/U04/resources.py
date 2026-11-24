"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, MSL:1-MSL:4).

The carrier is the UAES materials reader; checker.py extends it at run time with the revision 3 clause methods.
"""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U04",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
        ".xls": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
        ".xlsm": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
        ".xlsx": "audit.checker_lib.uaes.materials:UaesMaterialsAgent",
    },
    "methods": {"MSL:1": "msl_1", "MSL:2": "msl_2", "MSL:3": "msl_3", "MSL:4": "msl_4"},
}

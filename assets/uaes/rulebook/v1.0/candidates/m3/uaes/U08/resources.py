"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, CP:1-CP:13).

The control plan reader is the UAES one; checker.py extends it with the revision 3 clause methods.
"""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U08",
    "carriers": {
        ".docx": "audit.checker_lib.uaes.control_plan:UaesControlPlanAgent",
        ".pdf": "audit.checker_lib.uaes.control_plan:UaesControlPlanAgent",
        ".xls": "audit.checker_lib.uaes.control_plan:UaesControlPlanAgent",
        ".xlsm": "audit.checker_lib.uaes.control_plan:UaesControlPlanAgent",
        ".xlsx": "audit.checker_lib.uaes.control_plan:UaesControlPlanAgent",
    },
    "methods": {f"CP:{number}": f"rule_{number}" for number in range(1, 14)},
}

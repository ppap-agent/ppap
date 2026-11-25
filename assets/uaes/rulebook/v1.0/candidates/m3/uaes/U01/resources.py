"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, IR:1-IR:4)."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U01",
    "carriers": {
        ".pdf": "checker:InspectionR3",
        ".xls": "checker:InspectionR3",
        ".xlsm": "checker:InspectionR3",
        ".xlsx": "checker:InspectionR3",
    },
    "methods": {f"IR:{number}": f"rule_{number}" for number in range(1, 5)},
}

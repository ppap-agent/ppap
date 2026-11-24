"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, PSW:1-PSW:7)."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U17",
    "carriers": {
        ".docx": "checker:WarrantR3",
        ".pdf": "checker:WarrantR3",
        ".xls": "checker:WarrantR3",
        ".xlsm": "checker:WarrantR3",
        ".xlsx": "checker:WarrantR3",
    },
    "methods": {f"PSW:{number}": f"rule_{number}" for number in range(1, 8)},
}

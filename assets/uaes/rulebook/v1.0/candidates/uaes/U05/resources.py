"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, MSA:1-MSA:6).

The carrier is the UAES MSA reader; checker.py extends it at run time with the revision 3 clause methods.
"""

_CARRIER = "audit.checker_lib.uaes.msa:UaesMsaAgent"

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U05",
    "carriers": {".pdf": _CARRIER, ".xls": _CARRIER, ".xlsm": _CARRIER, ".xlsx": _CARRIER},
    "methods": {f"MSA:{number}": f"rule_{number}" for number in range(1, 7)},
}

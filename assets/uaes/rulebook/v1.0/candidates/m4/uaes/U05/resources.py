"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U05",
    "carriers": {
        ".pdf": "audit.checker_lib.uaes.v1.msa_r4:UaesMsaAgentR4",
        ".xls": "audit.checker_lib.uaes.v1.msa_r4:UaesMsaAgentR4",
        ".xlsm": "audit.checker_lib.uaes.v1.msa_r4:UaesMsaAgentR4",
        ".xlsx": "audit.checker_lib.uaes.v1.msa_r4:UaesMsaAgentR4",
    },
    "methods": {"MSA:1": "rule_1", "MSA:2": "rule_2", "MSA:3": "rule_3", "MSA:4": "rule_4", "MSA:5": "rule_5", "MSA:6": "rule_6"},
}

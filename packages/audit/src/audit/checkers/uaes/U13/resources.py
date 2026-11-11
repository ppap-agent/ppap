"""Implementation and clause methods this leaf runs."""

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U13",
    "carriers": {
        ".jpeg": "audit.checker_lib.uaes.records:UaesPhotoAgent",
        ".jpg": "audit.checker_lib.uaes.records:UaesPhotoAgent",
        ".pdf": "audit.checker_lib.uaes.records:UaesPhotoAgent",
        ".png": "audit.checker_lib.uaes.records:UaesPhotoAgent",
        ".webp": "audit.checker_lib.uaes.records:UaesPhotoAgent",
    },
    "methods": {"U13:1": "rule_1"},
}

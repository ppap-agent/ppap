"""Implementation and clause methods this candidate leaf runs (UAES v1.0 revision 3, PHOTO:1).

The carrier is the UAES photo reader, for every format a submitted photo may come in: the clause judges a document
container as well (not passing). checker.py extends it at run time with the revision 3 clause method.
"""

_PHOTO = "audit.checker_lib.uaes.records:UaesPhotoAgent"

RESOURCE = {
    "customer": "UAES",
    "preset_key": "U13",
    "carriers": {suffix: _PHOTO for suffix in (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif", ".pdf", ".xlsx", ".xlsm", ".xls", ".docx", ".doc")},
    "methods": {"PHOTO:1": "photo_1"},
}

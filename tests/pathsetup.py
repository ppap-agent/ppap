"""Put src-layout packages on sys.path before the `packages/` directory.

`packages/` holds importable `ppap_core` and also project trees such as
`ppap_excel_audit_engine/` (contents live under `src/`). Putting only
`packages/` first makes `ppap_excel_audit_engine` a namespace package
without `app_runtime`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PATHS = [
    ROOT / "packages" / "ppap_excel_audit_engine" / "src",
    ROOT / "packages" / "ppap_docops" / "src",
    ROOT / "packages" / "ppap_ocr_adapter" / "src",
    ROOT / "packages" / "ppap_pdf_to_excel" / "src",
    ROOT / "packages" / "ppap_vision" / "src",
    ROOT / "packages",
    ROOT / "services" / "api",
    ROOT / "services" / "worker",
    ROOT,
]
for _path in reversed(_PATHS):
    _text = str(_path)
    while _text in sys.path:
        sys.path.remove(_text)
    sys.path.insert(0, _text)

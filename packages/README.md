# Packages

Reusable application and audit libraries live here. Exact package names may change during migration, but responsibilities must stay explicit.

Current:

| Package | Owns |
|---|---|
| `ppap_core` | typed settings, DB/object-store probes, Responses helpers, identity/file contracts, engine audit binding, draft identification, product rule snapshots, audit-round statuses, visible finding mapping |
| `ppap_excel_audit_engine` | rule routing and checkers migrated from `ppap-agent` @ `53a5bd2b` |
| `ppap_docops` | Excel/PDF/DOCX extraction and conversion utilities |
| `ppap_pdf_to_excel` | PDF → workbook pipeline |
| `ppap_vision` | image extraction; seal/signature detectors stay lazy |
| `ppap_ocr_adapter` | optional OCR providers; missing config is disabled, not a fake fallback |

Source commit and exclusions: [SOURCE.md](SOURCE.md). Customer/business corpus is not in this repository. `python tools/stack.py verify-engine` proves Worker execution against a ready file version; it is not #3/#6 corpus acceptance and not S01.

Packages are libraries, not hidden microservices. Network boundaries require an explicit architecture reason.

Each migrated package must record its source repository commit and retain or improve the source regression tests.

Reusable application and audit libraries live here. Exact package names may change during migration, but responsibilities must stay explicit.

Current:

| Package | Owns |
|---|---|
| `ppap_core` | typed settings, DB/object-store probes, Responses URL helpers, capability flags |

Expected migration areas from `ppap-agent/ppap-agent`:

| Target area | Current source concept |
| --- | --- |
| `core` | shared contracts, settings, rule profiles, common types |
| `engine` | orchestration contracts and audit execution boundary |
| `docops` | document extraction/conversion/file utilities |
| `excel-audit-engine` | plugin-style rule/checker runtime |
| `ocr-adapter` | pluggable OCR providers |
| `pdf-to-excel` | PDF -> workbook preparation pipeline |
| `vision` | image/seal/signature processing |

Packages are libraries, not hidden microservices. Network boundaries require an explicit architecture reason.

Each migrated package must record its source repository commit and retain or improve the source regression tests.

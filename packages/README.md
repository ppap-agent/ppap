# Packages

Reusable application and audit libraries live here. Exact package names may change during migration, but responsibilities must stay explicit.

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

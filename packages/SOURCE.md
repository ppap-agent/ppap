# Migrated engine source

Pinned source: `ppap-agent/ppap-agent` commit `53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9`.

Moved into this repo:

- `ppap_excel_audit_engine`
- `ppap_docops`
- `ppap_pdf_to_excel`
- `ppap_vision`
- `ppap_ocr_adapter`
- `ppap_core` contracts, types, rule_profile, token_tracker

Not moved:

- `ppap_db` product ledger
- `lark_adaptor`
- HTTP/orchestrator app
- `assets/` and any customer/business corpus

Product `ppap_core.settings.Settings` remains the typed configuration owner (`LLM_*`). Copied checkers that still read `OPENAI_*` attributes receive a one-way projection of `LLM_*`. LLM transport is Responses (`store:false`, `stream:true`), not Chat Completions.

This does not authorize copying private samples into this repository.

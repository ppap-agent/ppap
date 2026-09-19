# Engine Worker

Asynchronous execution process for PPAP audits.

Start with `python tools/stack.py up`. The worker is a separate process; stopping it must not block API `/health`. Isolated-dev currently heartbeats and claims `engine.jobs`; rule execution is later Issues.

## Owns

- PostgreSQL job claim/lease/retry
- document preparation
- Excel/PDF/DOCX processing
- OCR selection/execution
- PDF-to-Excel pipeline
- audit rule execution
- PASS / NG / ERR findings and evidence
- append of product result revisions after execution

## Does not own

- public user HTTP routes
- login/session management
- product navigation
- tenant/organization administration
- a second audit ledger

## Engine packages

The worker should be thin. Reusable audit logic lives under `packages` so it can also be used by regression tests, offline tools, and controlled customer deployments without inventing another network service.

If legacy `.doc` is supported, the worker delegates conversion to the isolated converter sandbox instead of embedding an unrestricted LibreOffice subprocess.

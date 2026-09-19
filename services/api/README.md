# Product API

The only public HTTP API for PPAP.

Start the isolated-dev process with `python tools/stack.py up`. Do not run heavy document or model work in request threads.

## HTTP surface (#2)

- `GET /health` — process alive
- `GET /ready` — PostgreSQL and object storage actually reachable
- `GET /version` — `git_sha`
- `GET /public-config` — web-safe origin, profile, git sha; never secrets
- `GET /capabilities` — optional integration flags (`configured` / `disabled` only; not GitHub probe success)

## Owns

- product authentication/session handling
- tenant/user/external identity projection
- document metadata and immutable version registration
- audit-round acceptance and idempotency
- result, evidence, and history reads
- human review writes
- usage ledger queries and adapter synchronization
- Feishu OAuth/Drive integration endpoints required by the product

## Does not own

- OCR
- LibreOffice
- heavy PDF rendering/conversion
- long LLM/audit execution
- engine lease/retry mechanics

## Submission contract

An accepted audit request creates the product audit round and initial engine job in one PostgreSQL transaction. The API does not synchronously call a public engine task service.

Any feature named 'recognition' that performs expensive document work is submitted as worker work; only cheap metadata validation belongs in request handlers.

# Product API

The only public HTTP API for PPAP.

Start the isolated-dev process with `python tools/stack.py up`. Do not run heavy document or model work in request threads.

## HTTP surface (#2 / #4)

- `GET /health` — process alive
- `GET /ready` — PostgreSQL and object storage actually reachable
- `GET /version` — `git_sha`
- `GET /public-config` — web-safe origin, profile, git sha, login mode; never secrets
- `GET /capabilities` — optional integration flags (`configured` / `disabled` only; not GitHub probe success)
- `GET /login/feishu` — redirect to Feishu authorize with one-time state
- `GET /login/feishu/callback` — exchange code, bind provider/app/tenant/subject, set session cookie
- `POST /integrations/feishu/events` — URL challenge and authenticated `app_ticket`
- `GET /me` — current session; 401 if missing/expired/revoked
- `POST /logout` — revoke session
- `GET /resources/{id}` — object read; 403 `access_denied` without leaking body
- `POST /isolated-dev/sessions` — fixture sessions only when `PPAP_PROFILE=isolated-dev`; hidden in production

Login membership is not review authorization. Identities are not merged by email. Isolated-dev fixture sessions are not S02.

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

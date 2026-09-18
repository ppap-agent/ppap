# Product API

The only public HTTP API for PPAP.

Start the isolated-dev process with `python tools/stack.py up`. Do not run heavy document or model work in request threads.

## HTTP surface (#2 / #4 / #5)

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

- `POST /files` — create a staging version (creator gets `read`, not review)
- `POST /files/{id}/versions` — another staging version of the same file
- `PUT /files/{id}/versions/{vid}/blob` — exclusive object write; second PUT is 409
- `POST /files/{id}/versions/{vid}/finalize` — hash stored bytes as SHA-256; ready versions never change
- `GET /files/{id}/versions/{vid}` — metadata; 403 `access_denied` without leaking body
- `GET /files/{id}/versions/{vid}/download` — ready bytes only; staging is 409
- `POST /isolated-dev/reap-staging` — isolated-dev only; marks expired staging `failed` and may delete those objects; never touches `ready` keys

Unready versions cannot be treated as originals. Object keys include version id and are never reused. Failed or expired staging can be reaped; `ready` objects are not deleted by that path. Isolated-dev fixture sessions are not S02. Synthetic bytes in `verify-uploads` are not S03.

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

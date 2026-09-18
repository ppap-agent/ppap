# Web Client

One PPAP product client for normal browser/PWA and Feishu H5.

Isolated-dev currently serves `apps/web` on `http://127.0.0.1:4173` via `python tools/stack.py up`. The page reads `/public-config`, `/ready`, `/me`, optional `?resource=` / `?file=` / `?draft=` / `?round=`, can stage+finalize a local file, attach ready versions to an editable draft, and submit an idempotent accept that writes `round-id` / `execution-state`. Reloading `?round=` polls GET and does not create another round. Fixture sessions from `stack.py seed-auth` only prove object authorization, not S02. Upload/download hooks prove version identity on synthetic bytes, not S03. Draft/submit hooks are not S01 and not S04 Live.

## Owns

- product navigation and presentation
- document/data-set preparation UI
- audit submission UI
- findings/evidence/result UI
- human review UI
- usage/history UI
- platform adapters for browser vs Feishu host capabilities

## Boundary

The web client calls only the Product API.

It must not:

- call engine `/tasks` or worker endpoints
- access PostgreSQL/PostgREST directly
- use object-store administrative credentials
- duplicate a Feishu-only product ledger/state model
- embed server-side Partner AI or Feishu secrets

Prefer shared product routes/state with small platform adapters instead of rebuilding `app` and `pc` as two products.

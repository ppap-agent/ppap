# Web Client

One PPAP product client for normal browser/PWA and Feishu H5.

Isolated-dev currently serves `apps/web` on `http://127.0.0.1:4173` via `python tools/stack.py up`. The page reads `/public-config`, `/ready`, `/me`, optional `?resource=` / `?file=` / `?draft=` / `?round=`, can stage+finalize a local file, attach ready versions to an editable draft, submit an idempotent accept, reopen the same round to see PASS/NG/ERR, coverage, and the frozen original, save a human review note that does not rewrite machine findings, and show tenant usage states without a fabricated balance. Concurrent review updates show `review-conflict` instead of silently overwriting. The web app manifest does not cache originals or credentials and does not promise offline audit. Fixture sessions from `stack.py seed-auth` only prove object authorization, not S02. Synthetic bytes are not S01. Review notes are not formal approval. Usage observations are not S07.

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

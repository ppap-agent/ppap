# Web Client

One PPAP product client for normal browser/PWA and Feishu H5.

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

# Consolidation Migration

## Principle

Migration is capability-by-capability. Repositories are evidence sources, not deployment units to copy 1:1.

The final runtime must not depend on a legacy service just because its behavior has not yet been migrated. Temporary compatibility must be explicit, measured, and have an exit gate.

## Source-to-target map

| Source | Target | What to carry forward | What not to carry forward |
| --- | --- | --- | --- |
| `ppap-web` | `apps/web` | PC UX, Feishu H5 behavior, product navigation, API expectations | Split product implementations and old backend coupling |
| `ppap-host` | `services/api`, migrations, web reference | Product tables, InsForge migration lessons, PC decoupling tests | Browser-direct PostgREST/functions, TypeScript demo engine |
| `ppap-agent` | `services/worker`, `packages`, `tests` | Audit engine, docops, OCR, PDF-to-Excel, vision, worker claim model, regression assets | Public engine task API as a browser dependency |
| `ppap-uac` | API integration behavior | Feishu OAuth, Drive/export semantics, identity mapping evidence | Chain-enterprise org tree, PPAP-specific UAC fork, embedded secrets |
| `ppap-biz` | API/data compatibility reference | Current file/audit/transformer contracts needed for cutover | Java file-audit/transformer runtime after cutover |
| `ppap-foundation` | Compatibility reference | Only platform behavior still required by the product | Gateway/UAC/platform runtime dependency |
| `hax` | Migration/ops evidence | Runtime facts, product decisions, cutover evidence | Runtime imports or secrets copied into code |

## Phases

### 0. Freeze contracts and security

- Record exact source branches/SHAs for each migrated behavior.
- Inventory current user-visible endpoints, tables, file flows, Feishu scopes, and usage semantics.
- Treat credentials found in source repositories as compromised material: rotate them and never copy defaults into this repo.
- Define target acceptance tests before replacing a behavior.

Exit gate: source behavior and target tests are traceable.

### 1. Establish one web product

- Move the current PC product experience into `apps/web`.
- Keep Feishu H5 as a platform mode, not a separate product state tree.
- Route all product data through the new API contract, initially with mocks/adapters if required.

Exit gate: no new UI work is added to legacy `ppap-web`/`ppap-host` except production fixes during migration.

### 2. Build Product API and `product` schema

- Implement product identity projection, document versions, audit rounds, result reads, reviews, and usage ledger.
- Add immutable object storage.
- Remove browser-direct database/PostgREST/function access.

Exit gate: web can complete product CRUD against the new API without Java platform dependencies.

### 3. Move engine packages and worker

- Move reusable audit packages from `ppap-agent` into `packages` with source SHA recorded.
- Implement `services/worker` around the proven PostgreSQL claim/lease model.
- Keep expensive recognition and conversion out of API handlers.
- Preserve offline regression and customer-format tests.

Exit gate: the same audit corpus produces accepted equivalent results through the new worker.

### 4. Migrate Feishu adapter

- Reuse the existing PPAP Feishu application identity.
- Implement OAuth identity mapping and Drive/export reads in/behind Product API.
- Persist exported files as immutable document versions before audit.
- Do not introduce a second ledger or `lark_adaptor` mainline.

Exit gate: Feishu and normal web create the same product objects and audit rounds.

### 5. Shadow and cut over

- Replay representative reads and writes against the new stack.
- Compare product state, rule findings, evidence, and usage semantics.
- Verify rollback before the first production canary.
- Keep legacy system authoritative until explicit cutover gates pass.

Exit gate: production traffic can run Web -> Product API -> Postgres/S3 -> Worker with no required Java hop.

### 6. Retire legacy PPAP path

After evidence and rollback windows are complete:

- stop PPAP Java UAC/gateway/file-audit/transformer dependencies
- stop the transition `ppap-host` runtime
- freeze/archive legacy product forks as appropriate
- keep `hax` operational evidence until replacement runbooks are complete

## Migration rule

Do not solve migration uncertainty by adding a permanent adapter layer. Every temporary dependency must have an owner, an observable use count, and a deletion condition.

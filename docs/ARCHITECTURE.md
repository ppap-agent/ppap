# Target Architecture

## Goal

PPAP is one product with one public API and one asynchronous execution plane. The architecture optimizes for fewer ownership boundaries, not for the smallest possible box count.

## Components

### `apps/web`

One product client serves normal browser/PWA and Feishu H5 entry modes. Platform-specific behavior belongs behind adapters. Product routes, state, and API contracts stay shared.

It owns user interaction only: preparing a data set, selecting document versions and rules, submitting an audit round, viewing findings/evidence, and recording human review.

### `services/api`

The Product API owns:

- authentication and product sessions
- tenant/user/identity projection
- document metadata and immutable document versions
- audit-round acceptance and idempotency
- result queries and human review
- usage queries and external usage synchronization state
- Feishu OAuth/Drive adapter endpoints when needed

It performs only bounded work. Expensive recognition is a worker job even if the UI calls the feature 'recognition'.

### `services/worker`

The worker owns:

- durable job claim/lease/retry
- document preparation and format handling
- OCR and PDF-to-Excel pipelines
- DOCX structure extraction
- audit rule execution
- PASS / NG / ERR and evidence production
- writing a new product result revision after successful execution

The worker is not a second API product.

### PostgreSQL

One PostgreSQL cluster is used so product acceptance and job creation can be atomic.

Two schemas preserve different lifecycles:

- `product`: durable user-visible truth
- `engine`: retryable execution state

Separate database roles enforce the boundary. Schema names alone are not security.

### Object storage

One bucket is sufficient if object keys are immutable. Database records identify the exact audited bytes with object key, SHA-256, size, MIME type, and source metadata.

Workers fetch objects through the object-store SDK or a short-lived signed URL. A localhost URL is never file identity.

### Optional legacy DOC converter

If Word 97-2003 `.doc` remains supported, LibreOffice stays outside both core processes in a network-disabled, resource-limited sandbox using local IPC. If `.doc` support is retired, this component disappears.

## Main command flow

~~~text
1. Web uploads/chooses a source file.
2. API stores an immutable document version and checksum.
3. Web submits an audit command with an idempotency key.
4. API transaction creates product.audit_round + engine.job.
5. Worker claims engine.job.
6. Worker prepares files and runs migrated audit packages.
7. Worker appends product.audit_result + findings/evidence.
8. Web reads product state through API.
9. Human review appends a product review record.
~~~

## Integration boundary

Feishu provides identity and file-source capabilities. The product maps a Feishu identity to its own `product.user` / `product.identity` records. A Feishu file is exported to immutable object storage before it becomes an auditable document version.

Partner AI may answer entitlement or billing-adapter questions. It does not own audit rounds, results, or the local usage ledger.

## Explicitly rejected runtime shapes

- Browser -> PostgREST/database/function directly
- Browser -> engine `/tasks`
- API -> synchronous heavy audit call
- API and worker in one process merely to reduce box count
- product audit round and engine task as one mutable row
- Feishu cloud content used as the historical file version
- a second PPAP ledger for Feishu
- permanent runtime calls to Java UAC/gateway/file-audit/transformer
- `lark_adaptor` as a parallel main product

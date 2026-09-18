# PPAP

Target consolidation repository for the PPAP document audit product.

This repository is the long-term home of the product runtime. The target is deliberately small: one web client, one public Product API, one asynchronous worker, one PostgreSQL cluster with two schemas, and one immutable object bucket. If legacy `.doc` support remains, a network-disabled LibreOffice converter is an additional sandbox, not a third product service.

## Target runtime

~~~text
Browser / PWA / Feishu H5
            |
            v
      services/api
   (only public HTTP API)
       |          |
       |          +------> Object storage
       |                    immutable originals
       v
    PostgreSQL
  product + engine
       |
       v
 services/worker
 document preparation
 rule execution
 PASS / NG / ERR / evidence
       |
       +---- optional legacy DOC converter sandbox
~~~

Two core processes are intentional:

- `services/api` owns product commands and short request/response work.
- `services/worker` owns long-running document preparation and audit execution.

The API does not call a public engine task service. It accepts a product command and creates durable engine work in PostgreSQL. The worker claims that work and executes the audit packages.

## Repository layout

| Path | Responsibility | Must not become |
| --- | --- | --- |
| `apps/web` | One product UI for browser, PWA, and Feishu H5 | A direct database, object-store, or engine client |
| `services/api` | Identity, document versions, audit rounds, result reads, reviews, usage | OCR, LibreOffice, PDF rendering, or long audit execution |
| `services/worker` | Document preparation, OCR/PDF/DOCX work, rule execution, result production | A public user-facing HTTP service |
| `packages` | Reusable product and audit libraries migrated from `ppap-agent` | Hidden network services |
| `migrations` | Forward-only PostgreSQL schema changes for `product` and `engine` | Ad-hoc production SQL |
| `deploy` | Runtime composition and deployment contracts | Product logic |
| `tests` | Product, engine, and cross-boundary regression | A substitute for production evidence |
| `docs` | Architecture, data model, security, migration, and durable decisions | Runtime secrets |

## Hard architecture invariants

1. `services/api` is the only public product HTTP API.
2. `apps/web` never calls the engine, PostgreSQL, S3, PostgREST, or serverless database functions directly.
3. `services/worker` is asynchronous. It claims durable work from PostgreSQL and does not serve end users.
4. PostgreSQL has two responsibility boundaries: `product` for product truth and `engine` for execution state. They are not collapsed into one generic `tasks` table.
5. Product acceptance and engine job creation happen in one database transaction.
6. Uploaded and Feishu-exported originals are immutable object versions identified by object key and SHA-256.
7. Audit results are append-only revisions. A retry may create a new engine attempt and result revision; it must not erase prior evidence.
8. Feishu is an identity and file-source adapter. It is not a second PPAP product or a second ledger.
9. Partner AI is a usage/entitlement adapter. Local usage events remain the product record.
10. Legacy repositories are migration sources and behavioral references. The final runtime must not depend on their deployed services or source trees.

## Source repositories

| Repository | Use during consolidation | Final runtime dependency |
| --- | --- | --- |
| `ppap-agent/ppap-agent` | Audit packages, worker behavior, regression assets, deployment evidence | No. Its reusable code moves here before cutover. |
| `ppap-agent/ppap-web` | Current PC and Feishu UI behavior | No |
| `ppap-agent/ppap-host` | Transition InsForge schema/API experiments and PC migration evidence | No |
| `ppap-agent/ppap-uac` | Current Feishu OAuth, Drive, and product orchestration behavior | No |
| `ppap-agent/ppap-biz` | Current `file-audit` / `transformer` behavior and data contracts | No |
| `ppap-agent/ppap-foundation` | Historical platform/UAC/gateway contracts needed for compatibility checks | No |
| `ppap-agent/hax` | Product truth, runtime evidence, migration facts, and operations manuals | Not a runtime dependency |

Product vocabulary remains defined in `hax/products/ppap/CONTEXT.md` until it is deliberately migrated here.

## Read next

- `docs/ARCHITECTURE.md` - process and responsibility boundaries
- `docs/DATA_MODEL.md` - `product` and `engine` schemas and evidence chain
- `docs/MIGRATION.md` - source-to-target migration sequence and exit gates
- `docs/SECURITY.md` - secrets, roles, object integrity, and converter isolation
- `docs/DECISIONS.md` - decisions that should not be reopened casually

## Definition of the target state

The consolidation is complete only when production user traffic follows:

~~~text
Web -> Product API -> PostgreSQL/Object Storage -> Worker
~~~

and no user-facing flow requires Java UAC, gateway, `file-audit`, `transformer`, a second PPAP ledger, a second Feishu app, direct PostgREST/function access from the browser, or the old engine HTTP task API.

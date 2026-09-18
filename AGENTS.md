# Repository Instructions

## Scope

These rules apply to the whole repository. More specific `AGENTS.md` files may tighten them but must not weaken the architecture boundaries below.

## Product boundary

- This repository is the final PPAP product runtime, not another transition host.
- Legacy repositories are read-only migration sources. Do not add runtime imports, git submodules, network dependencies, or deployment dependencies on `ppap-agent`, `ppap-host`, `ppap-web`, `ppap-uac`, `ppap-biz`, or `ppap-foundation`.
- `hax` may remain the operational evidence source during migration, but production code must run without it.

## Runtime boundary

- `apps/web` may call only `services/api` product endpoints.
- `services/api` is the only public HTTP API.
- `services/api` must not run OCR, LibreOffice, heavy PDF rendering, long LLM calls, or audit execution in request handlers.
- `services/worker` owns long-running preparation and audit execution. Do not expose it to browsers.
- Do not reintroduce a public `/tasks` engine API as a product dependency.
- Prefer the PostgreSQL job queue/lease model before adding a separate message broker. Add infrastructure only when measured requirements justify it.

## Data boundary

- Keep product truth in the `product` schema and execution state in the `engine` schema.
- Do not merge product audit rounds and engine jobs into one table.
- Creating an accepted audit round and its first engine job must be atomic.
- Results and reviews are append-only/versioned product records. Never overwrite historical evidence on retry.
- Persist immutable object keys and SHA-256 for audit inputs. Do not persist localhost URLs as file identity.

## Security

- Never copy credentials, tokens, private keys, webhook secrets, database passwords, or historical default secrets from source repositories.
- Frontend code must never contain server credentials or direct database/object-store administrative keys.
- API and worker use different database roles with least privilege.
- If legacy `.doc` conversion remains, keep LibreOffice in a network-disabled, resource-limited sandbox.

## Integrations

- Feishu is an adapter for identity, Drive/file export, and optional product notifications. It must not create a second workflow or ledger.
- Keep the existing PPAP Feishu application identity unless a deliberate product decision changes it.
- Partner AI is an external entitlement/usage adapter. Local usage events are authoritative for product history.
- `lark_adaptor` is not the mainline product path.

## Validation

- Changes to web/API/worker boundaries require integration tests that prove the boundary.
- Data model changes require migration tests and evidence-chain tests.
- Worker changes require deterministic regression tests against migrated PPAP audit assets.
- Any migration from a legacy repository must record source commit/ref and behavioral acceptance evidence.

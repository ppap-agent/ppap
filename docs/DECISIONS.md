# Durable Architecture Decisions

These decisions define the target consolidation. Reversing one requires a deliberate architecture decision with evidence, not a local convenience change.

1. **One final product repository.** Reusable audit engine code moves into this repository before final cutover.
2. **One product web application.** Browser/PWA/Feishu H5 are modes of one product, not separate ledgers.
3. **One public Product API.** Browser clients do not directly access engine APIs, PostgREST, database functions, or tables.
4. **Asynchronous worker boundary.** Long audit/recognition work does not execute in API request handlers.
5. **One PostgreSQL cluster, two schemas.** Product lifecycle and engine execution lifecycle remain separate.
6. **Product round != engine job.** Engine retries can change attempts; a product audit round keeps one business identity.
7. **Immutable originals.** Audits reference immutable object versions and checksums.
8. **Append-only evidence.** Result retries create revisions rather than replacing history.
9. **Feishu is an adapter.** It supplies identity/file-source capabilities and optional notifications, not a parallel PPAP product.
10. **Partner AI is an adapter.** Local usage history is retained even when external entitlement/usage systems are consulted.
11. **No legacy runtime dependency at the finish line.** Java UAC/gateway/file-audit/transformer and transition hosts are migration sources, not permanent dependencies.
12. **Optional converter is a sandbox.** If legacy DOC support exists, process isolation is more important than preserving a two-box slogan.

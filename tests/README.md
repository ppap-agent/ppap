# Tests

Tests should prove product behavior and architecture boundaries, not only individual functions.

Expected suites:

- `product`: API/domain/idempotency/review/usage tests
- `engine`: migrated checker and document-processing regressions
- `integration`: API -> PostgreSQL -> worker -> result flow
- `contract`: Feishu and Partner AI adapter contracts with safe fixtures/mocks
- `migration`: source behavior parity and schema migration tests
- `security`: role boundaries, object immutability, and no-browser-direct-backend checks

Critical regression assertions include:

- duplicate submit does not create duplicate audit rounds
- worker retry does not erase prior result/evidence
- exact document-version checksum is preserved through audit
- web has no direct engine/database/PostgREST dependency
- API request path does not execute heavy OCR/audit work
- Feishu and normal web create the same product-domain objects

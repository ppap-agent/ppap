# Database Migrations

One PostgreSQL cluster, two responsibility schemas:

- `product`: product truth and user-visible history
- `engine`: execution queue, attempts, leases, and diagnostics

Migrations are forward-only and reviewed with the application change that needs them. Apply with `python tools/stack.py migrate` or `python tools/stack.py up`.

Rules:

- do not collapse audit rounds and engine jobs into one generic task table
- keep result/history records versioned or append-only
- add constraints for idempotency and foreign-key traceability
- do not embed credentials or environment-specific endpoints in migrations
- migration tooling uses an administrative role separate from API and worker runtime roles
- migrations must be testable from an empty database and from the latest supported previous schema

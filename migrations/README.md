# Database Migrations

One PostgreSQL cluster, two responsibility schemas:

- `product`: product truth and user-visible history
- `engine`: execution queue, attempts, leases, and diagnostics

Migrations are forward-only and reviewed with the application change that needs them.

The canonical migration order is the lexical order of the numbered files:

```text
migrations/0001_*.sql
migrations/0002_*.sql
...
```

There is no second ordering manifest. `migrations/apply.sh` sets `LC_ALL=C`, discovers `NNNN_*.sql` from its directory, and executes each file with `psql -v ON_ERROR_STOP=1 -f`. Each numbered migration records itself in `product.schema_migrations` and is safe to re-run.

Empty cluster:

```sh
psql -d postgres -v ON_ERROR_STOP=1 -f migrations/bootstrap.sql
PPAP_PG_DATABASE=ppap PGUSER=ppap_migrator PGPASSWORD='<password>' sh migrations/apply.sh
```

`bootstrap.sql` creates least-privilege roles (`ppap_migrator`, `ppap_api`, `ppap_worker`) and the application database from `PPAP_PG_*` environment variables. Compose, InsForge, TKE, and Schema CI all execute the same `apply.sh` path. Empty → latest、previous → latest、repeated apply、history ordering and fail-closed behavior are verified against real PostgreSQL by `pytest tests/schema`.

Rules:

- numbered migration filenames are the only ordering authority
- do not maintain an `apply.sql` or another explicit migration list
- do not collapse audit rounds and engine jobs into one generic task table
- keep result/history records versioned or append-only
- add constraints for idempotency and foreign-key traceability
- do not embed credentials or environment-specific endpoints in migrations
- migration tooling uses an administrative role separate from API and worker runtime roles
- migrations must be testable from an empty database and from the latest supported previous schema

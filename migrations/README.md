# Database Migrations

One PostgreSQL cluster, two responsibility schemas:

- `product`: product truth and user-visible history
- `engine`: execution queue, attempts, leases, and diagnostics

Migrations are forward-only and reviewed with the application change that needs them.

Apply with `psql`. Empty cluster:

```sh
psql -d postgres -v ON_ERROR_STOP=1 -f migrations/bootstrap.sql
psql -d ppap -v ON_ERROR_STOP=1 -f migrations/apply.sql
```

`bootstrap.sql` creates least-privilege roles (`ppap_migrator`, `ppap_api`, `ppap_worker`) and the application database from `PPAP_PG_*` environment variables. `apply.sql` `\ir`s the numbered files in order. Each numbered file records itself in `product.schema_migrations` and is safe to re-run.

Compose `init-db` retries the same two `psql` invocations until Postgres has finished its first-boot restart. Empty → latest、previous → latest 以及其余真实 PostgreSQL 契约：`python -m unittest discover -s tests/schema -v`（需要 isolated-dev postgres；测试使用容器内 `psql`，不要求 host `psql`）。

Rules:

- do not collapse audit rounds and engine jobs into one generic task table
- keep result/history records versioned or append-only
- add constraints for idempotency and foreign-key traceability
- do not embed credentials or environment-specific endpoints in migrations
- migration tooling uses an administrative role separate from API and worker runtime roles
- migrations must be testable from an empty database and from the latest supported previous schema

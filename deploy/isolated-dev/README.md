# isolated-dev stack

```sh
docker compose -f deploy/isolated-dev/compose.yaml up --build -d --wait
```

This Compose file is the isolated-dev product stack: PostgreSQL, [PGSTY Silo](https://github.com/pgsty/silo), `psql` bootstrap/migrations, the object bucket, API, Worker, and nginx Web. Credentials match [`.env.dev`](../../.env.dev). S3 inside the compose network is `http://silo:9000`; loopback ports stay published for host tools (`5432`, `9000`, `8080`, `4173`). Silo console stays inside the compose network on `:9001` and is not published.

Silo is AGPL and S3-compatible. Phase-1 production compose uses the same pinned classic image; the application still talks S3 through `PPAP_S3_*`.

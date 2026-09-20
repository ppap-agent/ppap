# isolated-dev stack

```sh
docker compose -f deploy/isolated-dev/compose.yaml up --build -d --wait
```

This Compose file is the isolated-dev product stack: PostgreSQL, [PGSTY Silo](https://github.com/pgsty/silo), `psql` bootstrap/migrations, the object bucket, API, Worker, and nginx Web. Credentials match [`.env.dev`](../../.env.dev). S3 stays on the compose network at `http://silo:9000` and is not published to the host. Loopback ports for host tools are `5432`, `8080`, and `4173`.

Silo is AGPL and S3-compatible. Phase-1 production compose uses the same pinned classic image; the application still talks S3 through `PPAP_S3_*`.

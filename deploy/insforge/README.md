# Self-hosted InsForge runtime

PPAP remains the product and domain authority. Self-hosted InsForge owns PostgreSQL, compute container lifecycle, secrets/env, runtime logs, and (when the OpenAI-compatible contract matches) the model gateway. This directory does not revive `ppap-host`.

Pinned InsForge release: [VERSION](VERSION) (`v2.3.2`).

## Trust boundary

Enabling Custom Compute mounts the host Docker socket into the InsForge container. That socket is host-root-equivalent. Keep the InsForge admin surface off the public internet.

## Install InsForge

Follow the upstream self-host path for the pinned tag, then turn on the Docker compute provider:

1. Install InsForge `v2.3.2` with Compose on the target host (`insforge-network` is the project network; the postgres service name is `postgres`).
2. In InsForge compose, uncomment the `docker.sock` volume on `insforge` and set `COMPUTE_PROVIDER=docker`.
3. Restart InsForge until logs show the Docker compute provider is ready.
4. Leave `COMPUTE_ISOLATE_NETWORK` off so API/Worker can resolve `postgres` by name.
5. Published compute ports should stay on loopback unless a gateway is in front (`COMPUTE_BIND_ADDRESS=127.0.0.1`).

Do not host PPAP API or Worker as Edge Functions. Worker work includes OCR, PDF, Excel, and LLM calls that must stay resident.

## PPAP database on the InsForge cluster

First phase keeps a separate database and the current least-privilege roles:

```text
InsForge PostgreSQL cluster
├─ InsForge own database/schemas
└─ ppap
   ├─ ppap_migrator
   ├─ ppap_api
   └─ ppap_worker
```

`PPAP_PG_HOST=postgres` on `insforge-network`. Bootstrap user/password are InsForge `POSTGRES_USER` / `POSTGRES_PASSWORD`. Then:

```sh
python tools/dev.py deploy --compose-file deploy/insforge/compose.yaml --env-file deploy/insforge/env.example
```

Fill secrets in a gitignored env file first. `init` is the only process that reads bootstrap/migrator credentials and runs `python tools/dev.py provision`. This overlay does not start PostgreSQL; it joins the existing InsForge network.

Do not import `ppap-host` schemas (`ppap_documents` / `ppap_jobs` / `ppap_findings`).

## Custom Compute specs

[compute/api.json](compute/api.json) and [compute/worker.json](compute/worker.json) are the InsForge Custom Compute contracts for current `ppap-api:<git-sha>` and `ppap-worker:<git-sha>` images.

- API: ingress `port`, HTTP 8080.
- Worker: ingress `none` (no public port).
- Environment matches the least-privilege interpolation in [compose.yaml](compose.yaml): API never holds bootstrap/migrator/worker passwords; Worker never holds API or Feishu secrets.

After images exist:

```sh
npx @insforge/cli compute deploy --name ppap-api --image ppap-api:<git-sha> --port 8080 --memory 1024
npx @insforge/cli compute deploy --name ppap-worker --image ppap-worker:<git-sha> --memory 2048
```

Set the same service env as the JSON specs (`--env-set`). Worker must stay `ingress=none` (the Docker provider default). The compose overlay is the PPAP-owned sibling path on the same network when you are not driving lifecycle through the InsForge compute API; Web stays a compose service and is not a Custom Compute prerequisite.

## Object storage and model gateway

PPAP keeps its S3 abstraction (`PPAP_S3_*`). Phase 1 keeps a dedicated [PGSTY Silo](https://github.com/pgsty/silo) for PPAP objects because the InsForge S3 gateway is a subset (default ~50MB uploads, no versioning). The compose service is the pinned classic image `docker.io/pgsty/silo:RELEASE.2026-09-16T00-00-00Z` (not `:latest`, not distroless). Silo still reads `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`. InsForge storage backup does not include those objects.

If InsForge Model Gateway speaks the current OpenAI-compatible contract, point `LLM_BASE_URL` at it. OCR and other special providers stay as they are unless that contract actually matches.

## Backup and restore

InsForge cluster backup and PPAP object backup are separate. `python tools/dev.py backup --compose-file deploy/insforge/compose.yaml --env-file …` dumps the `ppap` database through the init image against `PPAP_PG_HOST` (no compose `postgres` service) and archives Silo `/data` by mounting `silo-data` into that same init image (`tar` is not in the ubi-micro Silo image). Restore/rollback use the same split. Changing a compute image or env recreates the container and discards its filesystem; durable state stays in PostgreSQL and object storage.

## Web

Browsers still call only the PPAP Product API. They do not use InsForge Database/PostgREST or Storage admin APIs.

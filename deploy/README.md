# Deployment

Target production composition:

1. `web` static application
2. `api` core process
3. `worker` core process
4. PostgreSQL
5. S3-compatible object storage
6. optional network-disabled legacy DOC converter sandbox

Isolated-dev on this machine uses `python tools/stack.py up` (user-space PostgreSQL 16 + MinIO AGPL binary on 127.0.0.1). Optional Docker Compose infra is `deploy/isolated-dev/compose.yaml`; it is not required when the host cannot run a Docker daemon. Production must not inherit the `ppap_isolated_dev` credentials or the loopback MinIO endpoint.

The product has two core application processes: API and worker. Infrastructure processes and an optional security sandbox do not change that product boundary.

Deployment must supply secrets externally and identify deployed API/worker/engine code by immutable build or Git SHA.

Do not make `ppap-host`, Java PPAP services, or a separately deployed `ppap-agent` HTTP service permanent runtime dependencies.

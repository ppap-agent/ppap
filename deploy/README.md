# Deployment

Target production composition:

1. `web` static application
2. `api` core process
3. `worker` core process
4. PostgreSQL
5. S3-compatible object storage
6. optional network-disabled legacy DOC converter sandbox

The product has two core application processes: API and worker. Infrastructure processes and an optional security sandbox do not change that product boundary.

Deployment must supply secrets externally and identify deployed API/worker/engine code by immutable build or Git SHA.

Do not make `ppap-host`, Java PPAP services, or a separately deployed `ppap-agent` HTTP service permanent runtime dependencies.

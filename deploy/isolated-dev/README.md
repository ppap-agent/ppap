# isolated-dev infrastructure

`python tools/stack.py up` is the supported one-command start. It runs PostgreSQL and MinIO as local processes so Cloud Agent VMs without a usable Docker daemon can still boot the stack.

This Compose file is optional, for hosts that already run Docker. It uses the same isolated-dev credentials and loopback ports. Prefer `python tools/stack.py up` unless you are replacing only the data services.

MinIO community binaries are AGPL. They are an isolated-dev object store, not a production license decision. Production must supply its own S3-compatible endpoint and credentials.

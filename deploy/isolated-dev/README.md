# isolated-dev infrastructure

`python tools/stack.py up` is the supported one-command start. It runs PostgreSQL and [PGSTY Silo](https://github.com/pgsty/silo) as local processes so Cloud Agent VMs without a usable Docker daemon can still boot the stack. The Silo linux amd64 archive is pinned to the same release as production compose.

This Compose file is optional, for hosts that already run Docker. It uses the same isolated-dev credentials and loopback ports (S3 `:9000`, console `:9001`). Prefer `python tools/stack.py up` unless you are replacing only the data services.

Silo is AGPL and S3-compatible. Phase-1 production compose uses the same pinned classic image; the application still talks S3 through `PPAP_S3_*`.

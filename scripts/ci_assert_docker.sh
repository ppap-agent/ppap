#!/usr/bin/env bash
set -euo pipefail
fail() { echo "runner does not satisfy ppap-docker capability: $1" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || fail "docker CLI not found"
docker info --format 'Docker Engine server: {{.ServerVersion}}' 2>/dev/null || fail "docker info cannot reach the daemon"
docker compose version 2>/dev/null || fail "docker compose version failed"
docker buildx version 2>/dev/null || fail "docker buildx version failed"

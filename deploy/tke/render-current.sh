#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)"
exec python3 "$ROOT/deploy/tke/render-current.py"

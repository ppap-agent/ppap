#!/bin/sh
set -e
if [ "$PPAP_SERVICE" = "worker" ]; then
  exec python -m ppap_worker.main
fi
exec python -m uvicorn ppap_api:app --host 0.0.0.0 --port 8080

#!/bin/sh
set -e
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
if [ "$PPAP_SERVICE" = "worker" ]; then
  exec python -m ppap_worker.main
fi
if [ "$PPAP_SERVICE" = "deep" ]; then
  exec python -m ppap_deep
fi
exec python -m uvicorn ppap_api:app --host 0.0.0.0 --port 8080

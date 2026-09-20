#!/bin/sh
set -eu
api="${PPAP_PUBLIC_API_URL:-}"
api="${api%/}"
if [ -n "$api" ]; then
  printf '{"api_url":"%s"}\n' "$api" > /usr/share/nginx/html/runtime-config.json
fi
exec nginx -g "daemon off;"

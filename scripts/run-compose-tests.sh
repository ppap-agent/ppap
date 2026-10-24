#!/usr/bin/env bash
set -euo pipefail

suite="$1"
shift
compose=(docker compose "$@")

# Let Docker reserve free loopback ports, then pin those bindings for restarts.
export PPAP_API_HOST_PORT=0 PPAP_WEB_HOST_PORT=0 PPAP_TEST_POSTGRES_PORT=0
"${compose[@]}" up -d --wait --wait-timeout 180 --no-build
api_binding=$("${compose[@]}" port api 8080)
web_binding=$("${compose[@]}" port web 4173)
[[ "$api_binding" == 127.0.0.1:* && "$web_binding" == 127.0.0.1:* ]]
export PPAP_API_HOST_PORT="${api_binding##*:}"
export PPAP_WEB_HOST_PORT="${web_binding##*:}"
[[ "$PPAP_API_HOST_PORT" =~ ^[0-9]+$ && "$PPAP_WEB_HOST_PORT" =~ ^[0-9]+$ ]]
(( PPAP_API_HOST_PORT > 0 && PPAP_WEB_HOST_PORT > 0 ))
export PPAP_PUBLIC_API_URL="http://$api_binding"
export PPAP_WEB_URL="http://$web_binding"

# Apply the discovered browser origins and public links to the same stack.
"${compose[@]}" up -d --wait --wait-timeout 180 --no-build
[[ "$("${compose[@]}" port api 8080)" == "$api_binding" ]]
[[ "$("${compose[@]}" port web 4173)" == "$web_binding" ]]
printf 'Test stack API: %s; Web: %s\n' "$PPAP_PUBLIC_API_URL" "$PPAP_WEB_URL"
# Deliberately stress all E2E cases, including real API/Postgres/Worker paths,
# against the same isolated-dev stack. A failing test is evidence; never
# silently rerun it in serial or exclude it from the required CI Gate.
if [[ "$suite" == "tests/e2e" ]]; then
  case "${PPAP_E2E_WORKERS:-0}" in
    0)
      # Fast rollback: one process, same complete test collection.
      uv run --no-sync pytest "$suite" --durations=25 -vv
      ;;
    4)
      # All tests are eligible; load balances cases within large unittest classes.
      uv run --no-sync pytest "$suite" -n 4 --dist=load --max-worker-restart=0 --durations=25 -vv
      ;;
    *)
      echo "PPAP_E2E_WORKERS must be 0 or 4" >&2
      exit 2
      ;;
  esac
else
  uv run --no-sync pytest "$suite"
fi

#!/bin/sh
set -eu

: "${PPAP_PG_DATABASE:?PPAP_PG_DATABASE is required}"
LC_ALL=C
export LC_ALL

found=0
for migration in /migrations/[0-9][0-9][0-9][0-9]_*.sql; do
  if [ ! -f "$migration" ]; then
    continue
  fi
  found=1
  echo "applying ${migration##*/}"
  psql -d "$PPAP_PG_DATABASE" -v ON_ERROR_STOP=1 -f "$migration"
done

if [ "$found" -ne 1 ]; then
  echo "no numbered migrations found under /migrations" >&2
  exit 1
fi

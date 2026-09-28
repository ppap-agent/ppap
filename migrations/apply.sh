#!/bin/sh
set -eu

: "${PPAP_PG_DATABASE:?PPAP_PG_DATABASE is required}"
: "${PPAP_PG_MIGRATOR_USER:?PPAP_PG_MIGRATOR_USER is required}"
: "${PPAP_PG_MIGRATOR_PASSWORD:?PPAP_PG_MIGRATOR_PASSWORD is required}"
PGUSER="$PPAP_PG_MIGRATOR_USER"
PGPASSWORD="$PPAP_PG_MIGRATOR_PASSWORD"
export PGUSER PGPASSWORD
LC_ALL=C
export LC_ALL
MIGRATIONS_DIR="${MIGRATIONS_DIR:-$(CDPATH= cd -- "$(dirname "$0")" && pwd)}"

found=0
for migration in "$MIGRATIONS_DIR"/[0-9][0-9][0-9][0-9]_*.sql; do
  if [ ! -f "$migration" ]; then
    continue
  fi
  found=1
  echo "applying ${migration##*/}"
  psql -d "$PPAP_PG_DATABASE" -v ON_ERROR_STOP=1 -f "$migration"
done

if [ "$found" -ne 1 ]; then
  echo "no numbered migrations found under $MIGRATIONS_DIR" >&2
  exit 1
fi

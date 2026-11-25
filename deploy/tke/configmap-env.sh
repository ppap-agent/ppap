#!/bin/sh
# Print `KEY=value` lines of ConfigMap ppap-current (deploy/tke/current.yaml, the deployed values) whose key matches the ERE.
#   deploy/tke/configmap-env.sh '^PPAP_MODEL_(TEXT|VISION)_REASONING_EFFORT$'
set -eu
awk -v pattern="$1" '
  /^---/ { exit }
  /^data:/ { data = 1; next }
  data && /^  [A-Z0-9_]+: / {
    key = $1; sub(/:$/, "", key)
    value = $0; sub(/^  [A-Z0-9_]+: /, "", value); gsub(/^["\047]|["\047]$/, "", value)
    if (key ~ pattern) print key "=" value
  }
  data && /^[^ ]/ { data = 0 }
' "$(dirname "$0")/current.yaml"

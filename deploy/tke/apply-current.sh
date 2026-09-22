#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)"
KUBECONFIG="${KUBECONFIG:-$HOME/Projects/work/hax/runtime/tke-cls-6e71r2cz/headscale-ipv6.kubeconfig}"
export KUBECONFIG
kubectl -n ppap-test get secret ppap-current >/dev/null
kubectl -n ppap-test create configmap ppap-current-migrations \
  --from-file="$ROOT/migrations" --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$ROOT/deploy/tke/current.yaml"

#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)"
TKE="$ROOT/deploy/tke"
KUBECONFIG="${KUBECONFIG:-$HOME/Projects/work/hax/runtime/tke-cls-6e71r2cz/headscale-ipv6.kubeconfig}"
export KUBECONFIG

command -v kubectl >/dev/null 2>&1
command -v docker >/dev/null 2>&1
docker info >/dev/null

kubectl -n ppap-test get secret ppap-current >/dev/null
kubectl -n ppap-test get secret ppap-current-office >/dev/null

RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT HUP INT TERM
"$TKE/render-current.sh" > "$RENDERED"

# Admission/schema smoke and the real apply consume the exact same rendered
# object stream.
kubectl apply --dry-run=server -f "$RENDERED" >/dev/null
python3 "$TKE/preload-current.py" --objects "$RENDERED"

# No application or migration object is changed until both nodes pass preload.
# Numbered SQL filenames remain the only migration ordering authority.
kubectl -n ppap-test create configmap ppap-current-migrations \
  --from-file="$ROOT/migrations" --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$RENDERED"

for deployment in ppap-current-postgres ppap-current-silo; do
  kubectl -n ppap-test rollout status "deployment/$deployment" --timeout=300s
done
for job in ppap-current-init-db ppap-current-init-bucket; do
  kubectl -n ppap-test wait --for=condition=complete "job/$job" --timeout=300s
done
for deployment in ppap-current-office ppap-current-api ppap-current-worker ppap-current-web edition-proxy; do
  kubectl -n ppap-test rollout status "deployment/$deployment" --timeout=300s
done
for service in ppap-current-postgres ppap-current-silo ppap-current-office ppap-current-api ppap-current-web edition-proxy; do
  kubectl -n ppap-test get "service/$service" >/dev/null
done

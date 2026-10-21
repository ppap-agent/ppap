#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/../.." && pwd)"
kubeconfig="${PPAP_REMOTE_DEV_KUBECONFIG:-$repo_dir/../hax/runtime/tke-cls-6e71r2cz/headscale-ipv6.kubeconfig}"
key_dir="$repo_dir/.local/remote-dev"
key_path="$key_dir/bridge_ed25519"
kctl=(kubectl --kubeconfig "$kubeconfig")
compose=(docker compose -p ppap-remote-dev -f "$script_dir/compose.yaml")
tunnel_pids=()

need() {
  command -v "$1" >/dev/null || { echo "Missing command: $1" >&2; exit 1; }
}

preflight() {
  need kubectl
  if [[ "${1:-}" == native ]]; then
    need uv
    need pnpm
  else
    need docker
    docker compose version >/dev/null
    docker buildx version >/dev/null
  fi
  need jq
  need ssh
  need ssh-keygen
  need openssl
  need base64
  need nc
  need git
  test -f "$kubeconfig" || { echo "Kubeconfig not found: $kubeconfig" >&2; exit 1; }
  test -f "$repo_dir/.env" || { echo "Missing local $repo_dir/.env" >&2; exit 1; }
  "${kctl[@]}" -n ppap-test get service ppap-current-postgres ppap-current-silo >/dev/null
}

setup_bridge() {
  mkdir -p "$key_dir"
  chmod 700 "$key_dir"
  if [[ ! -f "$key_path" ]]; then
    (umask 077; ssh-keygen -q -t ed25519 -N '' -f "$key_path")
  fi
  "${kctl[@]}" create namespace ppap-remote-dev --dry-run=client -o yaml | "${kctl[@]}" apply -f -
  "${kctl[@]}" -n ppap-remote-dev create secret generic ppap-remote-dev-public-key \
    --from-file="authorized_keys=$key_path.pub" --dry-run=client -o yaml | "${kctl[@]}" apply -f -
  "${kctl[@]}" apply -f "$script_dir/bridge.yaml"
  "${kctl[@]}" -n ppap-remote-dev wait --for=condition=Ready pod/ppap-remote-dev-bridge --timeout=180s
}

setup_office() {
  if [[ -z "$("${kctl[@]}" -n ppap-remote-dev get secret ppap-remote-dev-office --ignore-not-found -o name)" ]]; then
    openssl rand -hex 48 | tr -d '\n' | "${kctl[@]}" -n ppap-remote-dev create secret generic ppap-remote-dev-office \
      --from-file=jwt-secret=/dev/stdin
  fi
  "${kctl[@]}" apply -f "$script_dir/onlyoffice.yaml"
  "${kctl[@]}" -n ppap-remote-dev rollout status deployment/ppap-remote-dev-office --timeout=600s
}

config_value() {
  jq -er --arg key "$1" '.data[$key]' <<< "$config_json"
}

secret_value() {
  jq -er --arg key "$1" '.data[$key]' <<< "$secret_json" | base64 -d
}

load_remote_settings() {
  local config_json secret_json office_secret_json
  config_json="$("${kctl[@]}" -n ppap-test get configmap ppap-current -o json)"
  secret_json="$("${kctl[@]}" -n ppap-test get secret ppap-current -o json)"
  export PPAP_REMOTE_PG_DATABASE="$(config_value PPAP_PG_DATABASE)"
  export PPAP_REMOTE_PG_API_USER="$(config_value PPAP_PG_API_USER)"
  export PPAP_REMOTE_PG_API_PASSWORD="$(secret_value PPAP_PG_API_PASSWORD)"
  export PPAP_REMOTE_TENANT_ALLOWLIST="$(config_value PPAP_CURRENT_TENANT_ALLOWLIST)"
  export PPAP_REMOTE_DEEP_CUSTOMER_ENABLED="$(config_value PPAP_DEEP_CUSTOMER_ENABLED)"
  export PPAP_REMOTE_DEEP_EXECUTOR_REVISION="$(config_value PPAP_DEEP_EXECUTOR_REVISION)"
  export PPAP_REMOTE_DEEP_SANDBOX_PROFILE="$(config_value PPAP_DEEP_SANDBOX_PROFILE)"
  export PPAP_REMOTE_S3_REGION="$(config_value PPAP_S3_REGION)"
  export PPAP_REMOTE_S3_BUCKET="$(config_value PPAP_S3_BUCKET)"
  export PPAP_REMOTE_S3_ACCESS_KEY="$(secret_value PPAP_S3_ACCESS_KEY)"
  export PPAP_REMOTE_S3_SECRET_KEY="$(secret_value PPAP_S3_SECRET_KEY)"
  export PPAP_BUILD_SHA="$(git -C "$repo_dir" rev-parse HEAD)"
  office_secret_json="$("${kctl[@]}" -n ppap-remote-dev get secret ppap-remote-dev-office -o json)"
  export PPAP_REMOTE_ONLYOFFICE_SECRET="$(jq -er '.data["jwt-secret"]' <<< "$office_secret_json" | base64 -d)"
}

wait_for_port() {
  local port="$1" i
  for ((i=0; i<40; i++)); do
    if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "Port $port did not become ready" >&2
  return 1
}

start_forward() {
  "${kctl[@]}" -n "$1" port-forward --address 127.0.0.1 "$2" "$3" >/dev/null &
  tunnel_pids+=("$!")
  wait_for_port "${3%%:*}"
}

stop_tunnels() {
  if ((${#tunnel_pids[@]})); then
    kill "${tunnel_pids[@]}" 2>/dev/null || true
    wait "${tunnel_pids[@]}" 2>/dev/null || true
  fi
}

run() {
  preflight
  if [[ "${1:-}" == live ]]; then
    compose+=(-f "$repo_dir/deploy/isolated-dev/web-dev.yaml" -f "$script_dir/api-dev.yaml")
  fi
  for port in 8080 4173 8082 15432 19000 12222; do
    if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
      echo "Local port $port is already in use. Stop the other stack first." >&2
      exit 1
    fi
  done
  setup_bridge
  setup_office
  load_remote_settings
  trap 'stop_tunnels; "${compose[@]}" down >/dev/null 2>&1 || true' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  start_forward ppap-test service/ppap-current-postgres 15432:5432
  start_forward ppap-test service/ppap-current-silo 19000:9000
  start_forward ppap-remote-dev pod/ppap-remote-dev-bridge 12222:2222
  start_forward ppap-remote-dev service/ppap-remote-dev-office 8082:80
  ssh -q -N -T -i "$key_path" -p 12222 \
    -o BatchMode=yes -o ExitOnForwardFailure=yes \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -R 0.0.0.0:8080:127.0.0.1:8080 dev@127.0.0.1 &
  tunnel_pids+=("$!")
  (
    cd "$repo_dir"
    docker buildx bake api web
  )
  "${compose[@]}" up --no-build -d --wait
  "${kctl[@]}" -n ppap-remote-dev exec ppap-remote-dev-bridge -- \
    wget -qO- http://127.0.0.1:8080/health >/dev/null
  echo "Remote dev is ready at http://127.0.0.1:4173/"
  echo "Keep this terminal open. Ctrl-C stops this stack and its tunnels."
  while true; do
    for pid in "${tunnel_pids[@]}"; do
      kill -0 "$pid" 2>/dev/null || { echo "A tunnel stopped" >&2; exit 1; }
    done
    sleep 2
  done
}

run_native() {
  preflight native
  for port in 8080 4173 8082 15432 19000 12222; do
    if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
      echo "Local port $port is already in use." >&2
      exit 1
    fi
  done
  setup_bridge
  setup_office
  load_remote_settings
  trap 'stop_tunnels' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  start_forward ppap-test service/ppap-current-postgres 15432:5432
  start_forward ppap-test service/ppap-current-silo 19000:9000
  start_forward ppap-remote-dev pod/ppap-remote-dev-bridge 12222:2222
  start_forward ppap-remote-dev service/ppap-remote-dev-office 8082:80
  ssh -q -N -T -i "$key_path" -p 12222 \
    -o BatchMode=yes -o ExitOnForwardFailure=yes \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -R 0.0.0.0:8080:127.0.0.1:8080 dev@127.0.0.1 &
  tunnel_pids+=("$!")
  (
    cd "$repo_dir"
    export PPAP_PROFILE=remote-dev PPAP_SERVICE=api
    export PPAP_PG_HOST=127.0.0.1 PPAP_PG_PORT=15432
    export PPAP_PG_DATABASE="$PPAP_REMOTE_PG_DATABASE"
    export PPAP_PG_API_USER="$PPAP_REMOTE_PG_API_USER"
    export PPAP_PG_API_PASSWORD="$PPAP_REMOTE_PG_API_PASSWORD"
    export PPAP_CURRENT_TENANT_ALLOWLIST="$PPAP_REMOTE_TENANT_ALLOWLIST"
    export PPAP_S3_ENDPOINT=http://127.0.0.1:19000
    export PPAP_S3_REGION="$PPAP_REMOTE_S3_REGION" PPAP_S3_BUCKET="$PPAP_REMOTE_S3_BUCKET"
    export PPAP_S3_ACCESS_KEY="$PPAP_REMOTE_S3_ACCESS_KEY" PPAP_S3_SECRET_KEY="$PPAP_REMOTE_S3_SECRET_KEY"
    export PPAP_WEB_URL=http://127.0.0.1:4173 PPAP_PUBLIC_API_URL=http://127.0.0.1:8080
    export PPAP_ONLYOFFICE_URL=http://127.0.0.1:8082
    export PPAP_ONLYOFFICE_API_URL=http://ppap-remote-dev-api.ppap-remote-dev.svc.cluster.local:8080
    export PPAP_ONLYOFFICE_SECRET="$PPAP_REMOTE_ONLYOFFICE_SECRET"
    export PPAP_DEEP_CUSTOMER_ENABLED="$PPAP_REMOTE_DEEP_CUSTOMER_ENABLED"
    export PPAP_DEEP_EXECUTOR_REVISION="$PPAP_REMOTE_DEEP_EXECUTOR_REVISION"
    export PPAP_DEEP_SANDBOX_PROFILE="$PPAP_REMOTE_DEEP_SANDBOX_PROFILE"
    export PPAP_USAGE_CHARGE_ENABLED=false LOG_FORMAT=text
    exec uv run --no-sync --env-file .env --package ppap-api python -m uvicorn ppap_api:app \
      --host 127.0.0.1 --port 8080 --reload --reload-dir packages --reload-dir services --no-access-log
  ) &
  tunnel_pids+=("$!")
  wait_for_port 8080
  (
    cd "$repo_dir/apps/web"
    export PPAP_PUBLIC_API_URL=http://127.0.0.1:8080
    exec pnpm exec vite --host 127.0.0.1 --port 4173 --strictPort
  ) &
  tunnel_pids+=("$!")
  wait_for_port 4173
  echo "Native remote-dev is ready at http://127.0.0.1:4173/"
  echo "Standard uses the existing cluster Worker. Ctrl-C stops only these local processes and tunnels."
  while true; do
    for pid in "${tunnel_pids[@]}"; do
      kill -0 "$pid" 2>/dev/null || { echo "A local process or tunnel stopped" >&2; exit 1; }
    done
    sleep 2
  done
}

case "${1:-up}" in
  up) run ;;
  up-live) run live ;;
  up-native) run_native ;;
  setup-bridge) preflight; setup_bridge ;;
  cleanup-bridge)
    preflight
    "${kctl[@]}" delete -f "$script_dir/bridge.yaml" --ignore-not-found
    "${kctl[@]}" -n ppap-remote-dev delete secret ppap-remote-dev-public-key --ignore-not-found
    ;;
  *) echo "Usage: $0 [up|up-live|up-native|setup-bridge|cleanup-bridge]" >&2; exit 2 ;;
esac

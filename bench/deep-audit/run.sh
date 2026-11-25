#!/usr/bin/env bash
# Run deep mode over the UAES oracle in the deep image built from HEAD.
#
#   bench/deep-audit/run.sh [--name NAME] [--jobs N] [--effort low|medium|high|xhigh] [-- deep_eval.py run options]
#
# Non-secret deep configuration is the deployed one (deploy/tke/current.yaml); the model endpoints
# and keys come from the repository-root .env. The models are the configured ones; only the
# review's reasoning effort can change (--effort). Results go to bench/deep-audit/runs/<name>/.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
NAME=""
JOBS=3
EFFORT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME=$2; shift 2 ;;
    --jobs) JOBS=$2; shift 2 ;;
    --effort) EFFORT=$2; shift 2 ;;
    --) shift; break ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || { echo "--jobs must be a positive integer" >&2; exit 2; }
[[ -z "$EFFORT" || "$EFFORT" =~ ^(low|medium|high|xhigh)$ ]] || { echo "--effort must be low, medium, high or xhigh" >&2; exit 2; }

cd "$ROOT"
# The image and the corpus must be the same revision: a dirty tree would evaluate code that no
# commit holds. Only what the deep image or the corpus reads counts (the web app does not).
if [[ -n "$(git status --porcelain --untracked-files=no -- packages apps/api apps/worker deploy assets tests/corpus pyproject.toml uv.lock)" ]]; then
  echo "commit or stash changes the deep image or the corpus reads first" >&2
  exit 2
fi
SHA=$(git rev-parse HEAD)
IMAGE="ppap-deep:${SHA}"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  PPAP_BUILD_SHA="$SHA" docker buildx bake --load deep
fi

NAME=${NAME:-$(date +%Y%m%d-%H%M)-${SHA:0:8}}
OUT="$ROOT/bench/deep-audit/runs/$NAME"
mkdir -p "$OUT"
chmod 0777 "$OUT"

ENV_FILE=$(mktemp)
trap 'rm -f "$ENV_FILE" "$ENV_FILE.next"' EXIT
deploy/tke/configmap-env.sh '^PPAP_(MODEL_[A-Z_]+_REASONING_EFFORT|DEEP_SANDBOX_PROFILE|DEEP_MODEL_MAX_REQUESTS|DEEP_EXECUTOR_REVISION)$' >"$ENV_FILE"
grep -E '^PPAP_MODEL_(TEXT|VISION|CODING)_(API_KEY|BASE_URL|MODEL)=' .env >>"$ENV_FILE"
echo "PPAP_BUILD_SHA=$SHA" >>"$ENV_FILE"
if [[ -n "$EFFORT" ]]; then
  grep -v '^PPAP_MODEL_CODING_REASONING_EFFORT=' "$ENV_FILE" >"$ENV_FILE.next"
  echo "PPAP_MODEL_CODING_REASONING_EFFORT=$EFFORT" >>"$ENV_FILE.next"
  mv "$ENV_FILE.next" "$ENV_FILE"
fi
REVIEW_MODEL=$(grep '^PPAP_MODEL_CODING_MODEL=' "$ENV_FILE" | cut -d= -f2-)
REVIEW_EFFORT=$(grep '^PPAP_MODEL_CODING_REASONING_EFFORT=' "$ENV_FILE" | cut -d= -f2-)

cat >"$OUT/meta.json" <<EOF
{"name": "$NAME", "commit": "$SHA", "image": "$IMAGE", "jobs": $JOBS, "review_model": "$REVIEW_MODEL", "review_effort": "$REVIEW_EFFORT", "options": "$*", "started": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF

pids=()
for ((shard = 0; shard < JOBS; shard++)); do
  docker run --rm --user 0:0 --env-file "$ENV_FILE" \
    -e PYTHONPATH=/repo/tests/corpus/contracts/src \
    -v "$ROOT:/repo:ro" -v "$OUT:/out" -w /repo \
    --entrypoint /app/.venv/bin/python "$IMAGE" \
    /repo/bench/deep-audit/deep_eval.py run --out "/out/shard-$shard" --shard "$shard/$JOBS" "$@" \
    >"$OUT/shard-$shard.log" 2>&1 &
  pids+=($!)
done
status=0
for pid in "${pids[@]}"; do wait "$pid" || status=1; done

cat "$OUT"/shard-*/cases.jsonl >"$OUT/cases.jsonl" 2>/dev/null || true
uv run --no-sync python bench/deep-audit/deep_eval.py report "$OUT" >/dev/null
echo "$OUT/summary.md"
exit $status

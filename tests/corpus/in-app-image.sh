#!/bin/bash
# Run a Python module or script inside the local ppap-app image, as close to the production Worker as a laptop gets:
# the same LibreOffice and converter sandbox (Excel rendering, EMF and legacy-format conversion fail on a macOS host),
# with this checkout's packages, apps, assets and tests mounted read-only over /app.
#
#   tests/corpus/in-app-image.sh -m corpus_contracts.candidate_run assets/uaes/rulebook/v1.0/candidates/m4/uaes/U18 --part F01R204778_水管内接头 --json
#
# The image is PPAP_APP_IMAGE, else the newest local ppap-app tag (build one with `docker buildx bake app` when there is none).
# The platform models are the PPAP_MODEL_TEXT_* / PPAP_MODEL_VISION_* lines of the repository-root .env, passed through a
# temporary env file that is removed afterwards and never printed; their reasoning effort is production's, from .env.production. Output goes to this shell; nothing in the checkout is written.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
image=${PPAP_APP_IMAGE:-$(docker images ppap-app --format '{{.Repository}}:{{.Tag}}' | head -1)}
[ -n "$image" ] || { echo "no local ppap-app image; set PPAP_APP_IMAGE or build one" >&2; exit 2; }
envfile=$(mktemp)
trap 'rm -f "$envfile"' EXIT
chmod 600 "$envfile"
grep -E '^PPAP_MODEL_(TEXT|VISION)_REASONING_EFFORT=' "$root/.env.production" > "$envfile"
[ -f "$root/.env" ] && grep -E '^PPAP_MODEL_(TEXT|VISION)_' "$root/.env" >> "$envfile" || true
docker run --rm --env-file "$envfile" -e PYTHONPATH=/app/tests/corpus/contracts/src \
  -v "$root/packages:/app/packages:ro" -v "$root/apps:/app/apps:ro" -v "$root/assets:/app/assets:ro" -v "$root/tests:/app/tests:ro" \
  --entrypoint /app/.venv/bin/python "$image" "$@"

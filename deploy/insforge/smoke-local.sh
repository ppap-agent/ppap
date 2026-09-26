#!/usr/bin/env bash
# Native InsForge lifecycle smoke using the pinned official CLI.
# This starts a throwaway local instance, checks status, runs `select 1`,
# and always stops. It does not print keys.
set -euo pipefail

CLI_VERSION="0.2.8"
CLI=(npx --yes "@insforge/cli@${CLI_VERSION}")
WORKDIR=""
STARTED=0

redact_json() {
  node --input-type=module -e '
    const chunks = []
    for await (const chunk of process.stdin) chunks.push(chunk)
    const raw = Buffer.concat(chunks).toString("utf8").trim()
    if (!raw) process.exit(0)
    const secret = /key|secret|password|token|credential|anon/i
    const scrub = value => {
      if (Array.isArray(value)) return value.map(scrub)
      if (value && typeof value === "object") {
        const next = {}
        for (const [key, item] of Object.entries(value)) {
          next[key] = secret.test(key) ? "[redacted]" : scrub(item)
        }
        return next
      }
      return value
    }
    try {
      process.stdout.write(JSON.stringify(scrub(JSON.parse(raw)), null, 2) + "\n")
    } catch {
      process.stdout.write("[non-json output omitted]\n")
    }
  '
}

cleanup() {
  if [[ -n "${WORKDIR}" && -d "${WORKDIR}" ]]; then
    if [[ "${STARTED}" -eq 1 ]]; then
      (cd "${WORKDIR}" && "${CLI[@]}" local stop --json >/dev/null 2>&1) || true
    fi
    rm -rf "${WORKDIR}"
  fi
}
trap cleanup EXIT

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/ppap-insforge-smoke.XXXXXX")"
cd "${WORKDIR}"

echo "InsForge CLI ${CLI_VERSION}: local start"
STARTED=1
"${CLI[@]}" local start --json | redact_json

echo "InsForge CLI ${CLI_VERSION}: local status"
"${CLI[@]}" local status --json | redact_json

echo "InsForge CLI ${CLI_VERSION}: db query"
"${CLI[@]}" db query "select 1" --json | redact_json

echo "InsForge CLI ${CLI_VERSION}: local stop"
"${CLI[@]}" local stop --json | redact_json
STARTED=0
echo "InsForge local lifecycle: ok"

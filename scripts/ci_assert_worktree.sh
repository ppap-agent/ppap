#!/usr/bin/env bash
set -euo pipefail
fail() { echo "CI checkout isolation violated: $1" >&2; exit 1; }
: "${GITHUB_WORKSPACE:?missing GitHub workspace}"
case "$PWD" in
  "$GITHUB_WORKSPACE"/.ci-checkouts/*) ;;
  *) fail "job is running in the shared checkout: $PWD" ;;
esac
[[ -d "$PWD/.git" ]] || fail "missing job-private Git metadata"
[[ "$(git rev-parse --show-toplevel)" == "$PWD" ]] || fail "Git root differs from job working directory"
echo "Isolated CI checkout for ${GITHUB_JOB:-local}: $PWD"

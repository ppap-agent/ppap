#!/usr/bin/env bash
set -euo pipefail
fail() { echo "CI checkout isolation violated: $1" >&2; exit 1; }
: "${GITHUB_WORKSPACE:?missing GitHub workspace}"
: "${RUNNER_NAME:?missing runner name}"
case "$RUNNER_NAME" in
  *[!a-zA-Z0-9._-]*|.|..) fail "runner name is not a safe checkout owner" ;;
esac
workspace=$(cd "$GITHUB_WORKSPACE" && pwd -P)
[[ "$(pwd -P)" == "$workspace/.ci-checkouts/$RUNNER_NAME" ]] || fail "job is outside its runner-owned checkout: $PWD"
[[ -d "$PWD/.git" ]] || fail "missing job-private Git metadata"
[[ "$(git rev-parse --show-toplevel)" == "$PWD" ]] || fail "Git root differs from job working directory"
echo "Runner-owned CI checkout for ${GITHUB_JOB:-local}: $PWD"

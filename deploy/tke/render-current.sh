#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)"
exec docker run --rm -v "$ROOT/deploy/tke:/src:ro" -w /src registry.k8s.io/kustomize/kustomize:v5.6.0 build .

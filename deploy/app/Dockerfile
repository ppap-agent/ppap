# API, Worker and Deep images, one graph. Layers go from what changes least to what changes every
# release: base image and system packages, third-party dependencies, fixed tools, then source.
# Helpers built in a separate stage enter at the base level whose inputs they belong to, so a release
# never rebuilds them and a rebuilt helper never invalidates the large layers below that level.
# The three base levels (system, deps, deep-base) are built and recorded by deploy/tke/bases.py;
# releases pass the recorded images here. Without them, the stages below are built in place.
ARG PPAP_SYSTEM_BASE=system
ARG PPAP_DEPS_BASE=deps
ARG PPAP_APP_BASE=deps

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS converter-builder
ARG PPAP_DEBIAN_MIRROR=
RUN set -eux; \
    if [ -n "$PPAP_DEBIAN_MIRROR" ]; then \
      sed -i "s#^URIs: http://deb.debian.org/debian#URIs: $PPAP_DEBIAN_MIRROR#" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    i=1; \
    while [ "$i" -le 5 ]; do \
      if apt-get -o Acquire::Retries=5 update \
        && apt-get -o Acquire::Retries=5 install -y --no-install-recommends gcc libc6-dev; then \
        break; \
      fi; \
      if [ "$i" -eq 5 ]; then exit 1; fi; \
      sleep $((i * 3)); \
      i=$((i + 1)); \
    done; \
    rm -rf /var/lib/apt/lists/*
COPY packages/documents/native/legacy_doc_sandbox.c /tmp/legacy_doc_sandbox.c
RUN gcc -O2 -Wall -Wextra -Werror /tmp/legacy_doc_sandbox.c -o /usr/local/bin/ppap-local-converter-sandbox

# Deep's Pi runner: its npm install depends only on the lock file.
FROM node:22.22.0-bookworm-slim@sha256:dd9d21971ec4395903fa6143c2b9267d048ae01ca6d3ea96f16cb30df6187d94 AS pi
WORKDIR /opt/ppap-pi
COPY deploy/deep/package.json deploy/deep/package-lock.json ./
RUN npm ci --ignore-scripts --registry=https://registry.npmjs.org
COPY deploy/deep/pi-runner.ts deploy/deep/pi-runner.test.mjs deploy/deep/tsconfig.json ./
# The runner is strict TypeScript; Node runs it directly (type stripping), tsc checks it here.
RUN npx tsc -p tsconfig.json \
 && node pi-runner.ts --version \
 && node --test pi-runner.test.mjs \
 && rm pi-runner.test.mjs tsconfig.json \
 && npm prune --omit=dev

# System layers: Debian packages and uv. They change only with this stage, never with uv.lock.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS system
COPY --from=ghcr.io/astral-sh/uv:0.9.26@sha256:9a23023be68b2ed09750ae636228e903a54a05ea56ed03a934d00fe9fbeded4b /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1
WORKDIR /app

ARG PPAP_DEBIAN_MIRROR=
RUN set -eux; \
    if [ -n "$PPAP_DEBIAN_MIRROR" ]; then \
      sed -i "s#^URIs: http://deb.debian.org/debian#URIs: $PPAP_DEBIAN_MIRROR#" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    i=1; \
    while [ "$i" -le 5 ]; do \
      if apt-get -o Acquire::Retries=5 update \
        && apt-get -o Acquire::Retries=5 install -y --no-install-recommends bash util-linux ca-certificates libgomp1 libglib2.0-0 libreoffice-writer libreoffice-calc libreoffice-draw fonts-noto-cjk; then \
        break; \
      fi; \
      if [ "$i" -eq 5 ]; then \
        exit 1; \
      fi; \
      sleep $((i * 3)); \
      i=$((i + 1)); \
    done; \
    rm -rf /var/lib/apt/lists/*

# Python dependency layers: they change with uv.lock, never with source.
FROM ${PPAP_SYSTEM_BASE} AS deps
COPY pyproject.toml uv.lock /app/
ARG PPAP_PIP_INDEX_URL=
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eux; \
    if [ -n "$PPAP_PIP_INDEX_URL" ]; then \
      export UV_DEFAULT_INDEX="$PPAP_PIP_INDEX_URL"; \
      uv venv /app/.venv; \
      uv export --quiet --frozen --no-dev --no-editable --no-emit-workspace --format requirements-txt -o /tmp/ppap-requirements.txt; \
      UV_LINK_MODE=copy uv pip install --python /app/.venv/bin/python --require-hashes --no-deps -r /tmp/ppap-requirements.txt; \
    fi; \
    UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 uv sync --frozen --no-dev --no-editable --no-install-workspace; \
    rm -f /tmp/ppap-requirements.txt
# The legacy-document sandbox changes only with its C source; built here, a release never reinstalls gcc
# (the runner's inline cache keeps only final-image layers, not the builder stage).
COPY --from=converter-builder /usr/local/bin/ppap-local-converter-sandbox /usr/local/bin/

# Deep's fixed tools sit on the dependency layers, below the source: a source change rebuilds none of them.
FROM ${PPAP_DEPS_BASE} AS deep-base
RUN useradd --uid 10001 --create-home --shell /bin/bash audit
COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /opt/ppap-pi /opt/ppap-pi
RUN chmod +x /opt/ppap-pi/pi-runner.ts \
 && ln -s /opt/ppap-pi/pi-runner.ts /usr/local/bin/ppap-pi
ENV PI_OFFLINE=1 PI_TELEMETRY=0

# The application: API and Worker on the dependency layers, Deep on deep-base. Same source either way.
FROM ${PPAP_APP_BASE} AS app
COPY packages /app/packages
COPY apps/api /app/apps/api
COPY apps/worker /app/apps/worker
# The authorization pages the API serves take their colours, type and corners from the Web design tokens.
COPY apps/web/tokens /app/apps/web/tokens
COPY deploy/app/entrypoint.sh /app/entrypoint.sh
# Workspace packages install editable: the venv points at /app/packages instead of carrying a second,
# byte-compiled copy, so a release pushes the source once (a few MB), not a ~15 MB install layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eux; \
    # The import checks below must not leave bytecode caches in this layer.
    export PYTHONDONTWRITEBYTECODE=1; \
    chmod +x /app/entrypoint.sh; \
    UV_LINK_MODE=copy uv sync --frozen --no-dev; \
    uv pip check --python /app/.venv/bin/python; \
    cd /; \
    /app/.venv/bin/python -c "import api, product, audit.engine, worker"; \
    /app/.venv/bin/python -c "from product.settings import Settings; from product.db import connect; from worker.deep_controller import open_store; from agent.source import authorize_execution; assert callable(authorize_execution(Settings(), connect=connect, open_store=open_store))"

# Read-only built-in template library (PPAP_TEMPLATE_ROOT).
COPY assets/li/templates /app/assets/li/templates
COPY assets/nio/templates /app/assets/nio/templates
COPY assets/uaes/templates /app/assets/uaes/templates
# Read-only rulebook revisions (assets/<customer>/rulebook), one line per customer that has one.
COPY assets/uaes/rulebook /app/assets/uaes/rulebook
COPY assets/fonts /app/assets/fonts

ARG PPAP_BUILD_SHA=
ARG PPAP_SERVICE=api
ENV PPAP_BUILD_SHA=${PPAP_BUILD_SHA}
ENV PPAP_SERVICE=${PPAP_SERVICE}
ENV PATH="/app/.venv/bin:${PATH}"
ENV PPAP_TEMPLATE_ROOT=/app/assets
EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]

# Deep: the Worker application with Pi. The audit, agent and PDF code it links to is the image's own
# /app source, the same revision the Controller runs.
FROM app AS deep
RUN mkdir -p /opt/agent/documents \
 && ln -s /app/packages/audit/src/audit /opt/agent/semantic \
 && ln -s /app/packages/agent/src/agent/deep_agent /opt/agent/deep_agent \
 && ln -s /app/packages/documents/src/documents/pdf /opt/agent/documents/pdf
RUN /usr/local/bin/ppap-pi --version \
 && /opt/ppap-pi/node_modules/.bin/pyright --version \
 && test -f /opt/agent/deep_agent/AGENTS.md \
 && /usr/bin/setpriv --help >/dev/null \
 && /usr/bin/prlimit --help >/dev/null \
 && /usr/bin/setpriv --reuid=10001 --regid=10001 --clear-groups -- \
      python /opt/agent/semantic/document_tools.py --help >/dev/null

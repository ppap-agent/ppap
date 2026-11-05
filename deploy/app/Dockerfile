FROM python:3.12-slim-bookworm AS converter-builder
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

FROM python:3.12-slim-bookworm
COPY --from=converter-builder /usr/local/bin/ppap-local-converter-sandbox /usr/local/bin/
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
        && apt-get -o Acquire::Retries=5 install -y --no-install-recommends ca-certificates libgomp1 libglib2.0-0 libreoffice-writer libreoffice-calc fonts-noto-cjk; then \
        break; \
      fi; \
      if [ "$i" -eq 5 ]; then \
        exit 1; \
      fi; \
      sleep $((i * 3)); \
      i=$((i + 1)); \
    done; \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock /app/
ARG PPAP_PIP_INDEX_URL=
# Keep third-party dependencies independent of workspace source changes.
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

COPY packages /app/packages
COPY apps/api /app/apps/api
COPY apps/worker /app/apps/worker
COPY apps/mcp-client /app/apps/mcp-client
COPY deploy/app/entrypoint.sh /app/entrypoint.sh
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eux; \
    chmod +x /app/entrypoint.sh; \
    UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 uv sync --frozen --no-dev --no-editable; \
    uv pip check --python /app/.venv/bin/python; \
    cd /; \
    /app/.venv/bin/python -c "import api, product, audit.engine, worker"; \
    /app/.venv/bin/python -c "from product.settings import Settings; from product.db import connect; from worker.deep_controller import open_store; from agent.source import authorize_execution; assert callable(authorize_execution(Settings(), connect=connect, open_store=open_store))"

ARG PPAP_BUILD_SHA=
ARG PPAP_SERVICE=api
ENV PPAP_BUILD_SHA=${PPAP_BUILD_SHA}
ENV PPAP_SERVICE=${PPAP_SERVICE}
ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]

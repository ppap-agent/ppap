FROM python:3.12-slim-bookworm AS converter-builder
RUN set -eux; \
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
COPY packages/ppap_docops/native/legacy_doc_sandbox.c /tmp/legacy_doc_sandbox.c
RUN gcc -O2 -Wall -Wextra -Werror /tmp/legacy_doc_sandbox.c -o /usr/local/bin/ppap-local-converter-sandbox

FROM python:3.12-slim-bookworm
COPY --from=converter-builder /usr/local/bin/ppap-local-converter-sandbox /usr/local/bin/
COPY --from=ghcr.io/astral-sh/uv:0.9.26@sha256:9a23023be68b2ed09750ae636228e903a54a05ea56ed03a934d00fe9fbeded4b /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock /app/
COPY packages /app/packages
COPY services /app/services
COPY deploy/app/entrypoint.sh /app/entrypoint.sh

ARG PPAP_DEBIAN_MIRROR=
ARG PPAP_PIP_INDEX_URL=
RUN set -eux; \
    if [ -n "$PPAP_DEBIAN_MIRROR" ]; then \
      sed -i "s#^URIs: http://deb.debian.org/debian\$#URIs: $PPAP_DEBIAN_MIRROR#" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    i=1; \
    while [ "$i" -le 5 ]; do \
      if apt-get -o Acquire::Retries=5 update \
        && apt-get -o Acquire::Retries=5 install -y --no-install-recommends ca-certificates libgomp1 libglib2.0-0 libreoffice-writer fonts-noto-cjk; then \
        break; \
      fi; \
      if [ "$i" -eq 5 ]; then \
        exit 1; \
      fi; \
      sleep $((i * 3)); \
      i=$((i + 1)); \
    done; \
    rm -rf /var/lib/apt/lists/*; \
    chmod +x /app/entrypoint.sh; \
    if [ -n "$PPAP_PIP_INDEX_URL" ]; then export UV_DEFAULT_INDEX="$PPAP_PIP_INDEX_URL"; fi; \
    UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 uv sync --frozen --no-dev --no-editable

ARG PPAP_BUILD_SHA=
ARG PPAP_SERVICE=api
ENV PPAP_BUILD_SHA=${PPAP_BUILD_SHA}
ENV PPAP_SERVICE=${PPAP_SERVICE}
ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]

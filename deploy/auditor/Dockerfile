# The auditor's environment: reading libraries, LibreOffice, Pi, and packages/auditor. Nothing else:
# no check programs, no product, no API. Build from the repository root:
#
#   docker build -f deploy/auditor/Dockerfile -t ppap-auditor .

# The same pinned Pi as the Deep guest (deploy/deep/package-lock.json).
FROM node:22.22.0-bookworm-slim@sha256:dd9d21971ec4395903fa6143c2b9267d048ae01ca6d3ea96f16cb30df6187d94 AS pi
WORKDIR /opt/pi
COPY deploy/deep/package.json deploy/deep/package-lock.json ./
RUN npm ci --ignore-scripts --omit=dev --registry=https://registry.npmjs.org \
 && rm -rf node_modules/pyright node_modules/.bin/pyright*

# The same base and system packages as the application images (deploy/app/Dockerfile, stage "system").
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

FROM system AS auditor
# The auditor's libraries at the versions uv.lock records.
COPY pyproject.toml uv.lock /app/
ARG PPAP_PIP_INDEX_URL=
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eux; \
    if [ -n "$PPAP_PIP_INDEX_URL" ]; then export UV_DEFAULT_INDEX="$PPAP_PIP_INDEX_URL"; fi; \
    uv export --quiet --frozen --no-dev --package auditor --no-emit-workspace --format requirements-txt -o /tmp/requirements.txt; \
    UV_LINK_MODE=copy uv pip install --system --require-hashes --no-deps -r /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt /app/pyproject.toml /app/uv.lock
COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /opt/pi /opt/pi
RUN useradd --uid 10001 --create-home --shell /bin/bash audit \
 && ln -s /opt/pi/node_modules/.bin/pi /usr/local/bin/pi \
 && printf '#!/bin/sh\nexec python -m auditor.tools "$@"\n' > /usr/local/bin/look \
 && chmod +x /usr/local/bin/look
COPY packages/auditor/src /opt/auditor
ENV PYTHONPATH=/opt/auditor PYTHONDONTWRITEBYTECODE=1 PI_OFFLINE=1 PI_TELEMETRY=0
RUN python -c "import auditor.run, auditor.tools, fitz, openpyxl, xlrd, docx, PIL, httpx" && pi --version && soffice --version
USER 10001:10001
ENV HOME=/home/audit
WORKDIR /work
ENTRYPOINT ["python", "-m", "auditor.run"]

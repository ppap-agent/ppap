ARG PPAP_APP_IMAGE=ppap-worker
FROM node:22.14.0-bookworm-slim@sha256:1c18d9ab3af4585870b92e4dbc5cac5a0dc77dd13df1a5905cea89fc720eb05b AS grok
WORKDIR /build
COPY deploy/deep/package.json deploy/deep/package-lock.json deploy/deep/verify-grok.mjs /build/
# Node 22.14.0 pins the npm CLI in this image. Registry signatures belong to
# npm. verify-grok.mjs is only the extra decompressed-executable allowlist.
RUN npm ci --ignore-scripts --registry=https://registry.npmjs.org \
 && npm audit signatures \
 && node /build/verify-grok.mjs

FROM ${PPAP_APP_IMAGE}
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home --shell /usr/sbin/nologin audit
COPY --from=grok /grok /usr/local/bin/grok
COPY packages/audit/src /app/packages/audit/src
COPY packages/agent/src /app/packages/agent/src
RUN mkdir -p /opt/agent \
 && ln -s /app/packages/audit/src/audit /opt/agent/semantic \
 && ln -s /app/packages/agent/src/agent/skill /opt/agent/skill
RUN /usr/local/bin/grok --version \
 && /usr/bin/setpriv --help >/dev/null \
 && /usr/bin/prlimit --help >/dev/null \
 && /usr/bin/setpriv --reuid=10001 --regid=10001 --clear-groups -- \
      python /opt/agent/semantic/document_tools.py --help >/dev/null

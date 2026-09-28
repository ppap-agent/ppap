ARG PPAP_APP_IMAGE
FROM node:22.14.0-bookworm-slim@sha256:1c18d9ab3af4585870b92e4dbc5cac5a0dc77dd13df1a5905cea89fc720eb05b AS grok
WORKDIR /build
COPY deploy/deep/verify-grok.mjs /build/verify-grok.mjs
# Registry signatures belong to the npm CLI. verify-grok.mjs only pins the
# decompressed guest executable, which npm does not hash.
RUN npm install --ignore-scripts --no-package-lock --registry=https://registry.npmjs.org @xai-official/grok@1.0.40 \
 && npm audit signatures \
 && node /build/verify-grok.mjs

FROM ${PPAP_APP_IMAGE}
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home --shell /usr/sbin/nologin audit
COPY --from=grok /grok /usr/local/bin/grok
COPY packages/ppap_deep /app/packages/ppap_deep
RUN mkdir -p /opt/ppap \
 && ln -s /app/packages/ppap_deep/src/ppap_deep/semantic /opt/ppap/semantic
RUN /usr/local/bin/grok --version \
 && /usr/bin/setpriv --help >/dev/null \
 && /usr/bin/prlimit --help >/dev/null \
 && /usr/bin/setpriv --reuid=10001 --regid=10001 --clear-groups -- \
      python /opt/ppap/semantic/document_tools.py --help >/dev/null

ARG PPAP_APP_IMAGE=ppap-worker
FROM node:22.22.0-bookworm-slim@sha256:dd9d21971ec4395903fa6143c2b9267d048ae01ca6d3ea96f16cb30df6187d94 AS pi
WORKDIR /opt/ppap-pi
COPY deploy/deep/package.json deploy/deep/package-lock.json deploy/deep/pi-runner.mjs deploy/deep/pi-runner.test.mjs ./
RUN npm ci --ignore-scripts --registry=https://registry.npmjs.org \
 && npm audit signatures \
 && node pi-runner.mjs --version \
 && node --test pi-runner.test.mjs \
 && rm pi-runner.test.mjs

FROM ${PPAP_APP_IMAGE}
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends bash util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home --shell /bin/bash audit
COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /opt/ppap-pi /opt/ppap-pi
RUN chmod +x /opt/ppap-pi/pi-runner.mjs \
 && ln -s /opt/ppap-pi/pi-runner.mjs /usr/local/bin/ppap-pi
ENV PI_OFFLINE=1 PI_TELEMETRY=0
COPY packages/audit/src /app/packages/audit/src
COPY packages/agent/src /app/packages/agent/src
COPY packages/documents/src/documents/pdf /app/packages/documents/src/documents/pdf
RUN mkdir -p /opt/agent/documents \
 && ln -s /app/packages/audit/src/audit /opt/agent/semantic \
 && ln -s /app/packages/agent/src/agent/skill /opt/agent/skill \
 && ln -s /app/packages/documents/src/documents/pdf /opt/agent/documents/pdf
RUN /usr/local/bin/ppap-pi --version \
 && /usr/bin/setpriv --help >/dev/null \
 && /usr/bin/prlimit --help >/dev/null \
 && /usr/bin/setpriv --reuid=10001 --regid=10001 --clear-groups -- \
      python /opt/agent/semantic/document_tools.py --help >/dev/null

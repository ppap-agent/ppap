ARG PPAP_APP_IMAGE=ppap-worker
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

FROM ${PPAP_APP_IMAGE}
USER root
# System tools are installed in the shared application dependency layer.
RUN useradd --uid 10001 --create-home --shell /bin/bash audit
COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /opt/ppap-pi /opt/ppap-pi
RUN chmod +x /opt/ppap-pi/pi-runner.ts \
 && ln -s /opt/ppap-pi/pi-runner.ts /usr/local/bin/ppap-pi
ENV PI_OFFLINE=1 PI_TELEMETRY=0
COPY packages/audit/src /app/packages/audit/src
COPY packages/agent/src /app/packages/agent/src
COPY packages/documents/src/documents/pdf /app/packages/documents/src/documents/pdf
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

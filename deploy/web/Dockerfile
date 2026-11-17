FROM node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a AS web-build
WORKDIR /app
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
COPY apps/web/package.json apps/web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web ./
RUN pnpm build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10

COPY --from=web-build /app/dist /usr/share/nginx/html
COPY apps/web/app.json /usr/share/nginx/html/
COPY deploy/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/web/entrypoint.sh /entrypoint.sh

ARG PPAP_BUILD_SHA=
ENV PPAP_BUILD_SHA=${PPAP_BUILD_SHA}

RUN printf '%s\n' "{\"git_sha\":\"${PPAP_BUILD_SHA}\"}" > /usr/share/nginx/html/build-identity.json \
    && rm -f /usr/share/nginx/html/runtime-config.json \
    && chmod +x /entrypoint.sh

EXPOSE 4173
ENTRYPOINT ["/entrypoint.sh"]

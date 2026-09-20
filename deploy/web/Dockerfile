FROM nginx:1.27-alpine

ARG PPAP_BUILD_SHA=
ENV PPAP_BUILD_SHA=${PPAP_BUILD_SHA}

COPY apps/web /usr/share/nginx/html
COPY deploy/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/web/entrypoint.sh /entrypoint.sh

RUN printf '%s\n' "{\"git_sha\":\"${PPAP_BUILD_SHA}\"}" > /usr/share/nginx/html/build-identity.json \
    && rm -f /usr/share/nginx/html/runtime-config.json \
    && chmod +x /entrypoint.sh

EXPOSE 4173
ENTRYPOINT ["/entrypoint.sh"]

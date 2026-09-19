FROM python:3.12-slim-bookworm

ARG PPAP_BUILD_SHA=
ENV PPAP_BUILD_SHA=${PPAP_BUILD_SHA}
ENV PYTHONUNBUFFERED=1
ENV PPAP_WEB_BIND=0.0.0.0
ENV PPAP_WEB_PORT=4173
WORKDIR /web

COPY apps/web /web
COPY tools/web_server.py /app/tools/web_server.py

RUN printf '%s\n' "{\"git_sha\":\"${PPAP_BUILD_SHA}\"}" > /web/build-identity.json

EXPOSE 4173
CMD ["python", "/app/tools/web_server.py"]

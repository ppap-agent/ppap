FROM python:3.12-slim-bookworm

ARG PPAP_BUILD_SHA=
ARG PPAP_SERVICE=api
ENV PPAP_BUILD_SHA=${PPAP_BUILD_SHA}
ENV PPAP_SERVICE=${PPAP_SERVICE}
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml /app/
COPY packages /app/packages
COPY services /app/services
COPY tools /app/tools
COPY migrations /app/migrations
COPY deploy/app/entrypoint.sh /app/entrypoint.sh

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 libglib2.0-0 postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && chmod +x /app/entrypoint.sh \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e /app

ENV PYTHONPATH=/app/packages:/app/packages/ppap_docops/src:/app/packages/ppap_excel_audit_engine/src:/app/packages/ppap_ocr_adapter/src:/app/packages/ppap_pdf_to_excel/src:/app/packages/ppap_vision/src:/app/services/api:/app/services/worker
EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]

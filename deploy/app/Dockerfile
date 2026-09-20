FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml /app/
COPY packages /app/packages
COPY services /app/services
COPY deploy/app/entrypoint.sh /app/entrypoint.sh

RUN set -eux; \
    i=1; \
    while [ "$i" -le 5 ]; do \
      if apt-get -o Acquire::Retries=5 update \
        && apt-get -o Acquire::Retries=5 install -y --no-install-recommends ca-certificates libgomp1 libglib2.0-0; then \
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
    pip install --no-cache-dir --upgrade pip; \
    pip install --no-cache-dir -e /app

ARG PPAP_BUILD_SHA=
ARG PPAP_SERVICE=api
ENV PPAP_BUILD_SHA=${PPAP_BUILD_SHA}
ENV PPAP_SERVICE=${PPAP_SERVICE}
ENV PYTHONPATH=/app/packages:/app/packages/ppap_docops/src:/app/packages/ppap_excel_audit_engine/src:/app/packages/ppap_ocr_adapter/src:/app/packages/ppap_pdf_to_excel/src:/app/packages/ppap_vision/src:/app/services/api:/app/services/worker
EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]

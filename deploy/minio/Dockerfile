FROM debian:bookworm-slim

ARG MINIO_RELEASE=RELEASE.2025-09-07T16-13-09Z
ARG MINIO_SHA256=7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f

ADD https://github.com/minio/minio/releases/download/${MINIO_RELEASE}/minio.linux-amd64.${MINIO_RELEASE} /usr/local/bin/minio
RUN echo "${MINIO_SHA256}  /usr/local/bin/minio" | sha256sum -c - \
    && chmod +x /usr/local/bin/minio

EXPOSE 9000
CMD ["minio", "server", "/data", "--address", ":9000"]

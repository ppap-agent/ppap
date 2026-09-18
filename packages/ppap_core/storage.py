from __future__ import annotations

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from ppap_core.settings import Settings


def s3_client(settings: Settings):
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def object_store_ready(settings: Settings) -> dict[str, str]:
    try:
        s3_client(settings).head_bucket(Bucket=settings.s3_bucket)
        return {"state": "ok"}
    except (BotoCoreError, ClientError, OSError):
        return {"state": "unavailable"}


def ensure_bucket(settings: Settings) -> None:
    client = s3_client(settings)
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)

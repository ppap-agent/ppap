from __future__ import annotations

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError, ParamValidationError

from product.files import ObjectExists, ObjectMissing, ObjectTooLarge
from product.settings import Settings


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


class S3Objects:
    """Exclusive object writes. Unique version keys are the product identity; If-None-Match is extra."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def put_exclusive(self, key: str, body: bytes, content_type: str) -> None:
        client = s3_client(self.settings)
        bucket = self.settings.s3_bucket
        try:
            client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type, IfNoneMatch="*")
            return
        except ParamValidationError:
            pass
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"PreconditionFailed", "412"}:
                raise ObjectExists() from exc
            raise
        try:
            client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code", "")) not in {"404", "NoSuchKey", "NotFound"}:
                raise
        else:
            raise ObjectExists()
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)

    def get(self, key: str, *, max_bytes: int | None = None) -> bytes:
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("max_bytes must be nonnegative")
        try:
            response = s3_client(self.settings).get_object(Bucket=self.settings.s3_bucket, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectMissing() from exc
            raise
        body = response["Body"]
        try:
            if max_bytes is None:
                return body.read()
            if response.get("ContentLength", 0) > max_bytes:
                raise ObjectTooLarge()
            data = bytearray()
            while chunk := body.read(min(1024 * 1024, max_bytes + 1 - len(data))):
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ObjectTooLarge()
            return bytes(data)
        finally:
            body.close()

    def delete(self, key: str) -> None:
        s3_client(self.settings).delete_object(Bucket=self.settings.s3_bucket, Key=key)

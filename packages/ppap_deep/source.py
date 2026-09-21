"""Controller file source: frozen file_version_id → object bytes. Not a Worker RPC field."""
from __future__ import annotations

from uuid import UUID

from ppap_core.db import connect
from ppap_core.files import ObjectMissing
from ppap_core.settings import Settings
from ppap_core.storage import S3Objects
from ppap_deep.client import DeepError
from ppap_deep.controller import FileSource


def object_file_source(settings: Settings) -> FileSource:
    objects = S3Objects(settings)

    def load(file_version_id: str) -> bytes:
        try:
            ident = str(UUID(str(file_version_id)))
        except (ValueError, TypeError) as exc:
            raise DeepError("deep_input_missing") from exc
        with connect(settings, "worker") as conn:
            row = conn.execute(
                "SELECT object_key, status FROM product.file_versions WHERE id = %s",
                (ident,),
            ).fetchone()
        if not row or row[1] != "ready" or not row[0]:
            raise DeepError("deep_input_missing")
        try:
            return objects.get(row[0])
        except ObjectMissing as exc:
            raise DeepError("deep_input_missing") from exc

    return load

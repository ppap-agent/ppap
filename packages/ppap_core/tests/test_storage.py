"""Object response limits and cleanup at the external S3 boundary."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from ppap_core.files import ObjectTooLarge
from ppap_core.settings import Settings
from ppap_core.storage import S3Objects


@pytest.mark.parametrize(
    "payload,declared,limit,too_large",
    [(b"abc", 3, 3, False), (b"", 0, 0, False), (b"abcd", 4, 3, True), (b"abcd", None, 3, True), (b"abcd", 1, 3, True)],
    ids=["exact-limit", "empty", "declared-oversize", "missing-size", "incorrect-size"],
)
def test_bounded_object_read_handles_short_reads_and_closes_body(payload, declared, limit, too_large):
    reads = []

    class Body(BytesIO):
        def read(self, size=-1):
            reads.append(size)
            assert 0 < size <= limit + 1
            return super().read(min(size, 1))

    body = Body(payload)
    response = {"Body": body}
    if declared is not None:
        response["ContentLength"] = declared
    client = SimpleNamespace(get_object=lambda **_kwargs: response)
    with patch("ppap_core.storage.s3_client", return_value=client):
        if too_large:
            with pytest.raises(ObjectTooLarge):
                S3Objects(Settings()).get("frozen-object", max_bytes=limit)
        else:
            assert S3Objects(Settings()).get("frozen-object", max_bytes=limit) == payload
    assert body.closed
    if declared is not None and declared > limit:
        assert reads == []


def test_object_read_closes_body_after_transport_failure():
    class Body(BytesIO):
        def read(self, size=-1):
            raise OSError("external transport interrupted")

    body = Body()
    with patch("ppap_core.storage.s3_client", return_value=SimpleNamespace(get_object=lambda **_kwargs: {"Body": body})):
        with pytest.raises(OSError):
            S3Objects(Settings()).get("frozen-object", max_bytes=3)
    assert body.closed

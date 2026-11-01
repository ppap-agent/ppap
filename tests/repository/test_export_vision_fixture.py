"""Export only existing, source-bound Responses evidence without copying URLs or credentials."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from documents.ocr.cache import VISION_CACHE_VERSION
from openpyxl import Workbook

from scripts.export_vision_fixture import export


def cache_entry(cache: Path, source: Path) -> tuple[str, dict]:
    cache.mkdir()
    model = "fixture-model"
    key = hashlib.md5(VISION_CACHE_VERSION.encode() + model.encode() + source.read_bytes()).hexdigest()
    workbook = Workbook()
    workbook.active["A1"] = "original OCR evidence"
    workbook.save(cache / "original.xlsx")
    workbook.close()
    (cache / "original.html").write_text("<p>original evidence</p>")
    payload = {"file_id": key, "vision_protocol": VISION_CACHE_VERSION, "vision_model": model, "excel_path": "original.xlsx", "html_path": "original.html"}
    with sqlite3.connect(cache / "ocr_cache.db") as connection:
        connection.execute("CREATE TABLE content_cache (file_id TEXT PRIMARY KEY, text TEXT)")
        connection.execute("CREATE TABLE url_cache (url_hash TEXT PRIMARY KEY, file_id TEXT)")
    return key, payload


def store(cache: Path, key: str, payload: dict) -> None:
    with sqlite3.connect(cache / "ocr_cache.db") as connection:
        connection.execute("INSERT INTO content_cache VALUES (?, ?)", (key, json.dumps(payload)))


def test_export_binds_source_and_protocol_and_omits_unrelated_metadata(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source bytes, not a provider invocation")
    cache, destination = tmp_path / "cache", tmp_path / "candidate"
    key, payload = cache_entry(cache, source)
    store(cache, key, {**payload, "unrelated_metadata": "DO_NOT_EXPORT"})
    store(cache, "another-source", payload)
    with sqlite3.connect(cache / "ocr_cache.db") as connection:
        connection.execute("INSERT INTO url_cache VALUES ('DO_NOT_EXPORT', ?)", (key,))
    before = (cache / "ocr_cache.db").read_bytes()

    assert export(cache, source, destination) == 1

    assert (cache / "ocr_cache.db").read_bytes() == before
    with sqlite3.connect(destination / "ocr_cache.db") as connection:
        rows = connection.execute("SELECT file_id, text FROM content_cache").fetchall()
        assert connection.execute("SELECT * FROM url_cache").fetchall() == []
    assert len(rows) == 1 and rows[0][0] == key
    copied = json.loads(rows[0][1])
    assert set(copied) == {"file_id", "vision_protocol", "vision_model", "excel_path", "html_path"}
    assert copied["vision_protocol"] == VISION_CACHE_VERSION
    for field in ("excel_path", "html_path"):
        assert not Path(copied[field]).is_absolute()
        assert (destination / copied[field]).read_bytes() == (cache / payload[field]).read_bytes()
    assert "DO_NOT_EXPORT" not in rows[0][1]


def test_legacy_record_is_not_relabelled_even_with_a_current_key(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    cache, destination = tmp_path / "cache", tmp_path / "candidate"
    key, payload = cache_entry(cache, source)
    del payload["vision_protocol"]
    store(cache, key, payload)
    before = (cache / "ocr_cache.db").read_bytes()

    assert export(cache, source, destination) == 0

    assert not destination.exists()
    assert (cache / "ocr_cache.db").read_bytes() == before


@pytest.mark.parametrize("symlink", [False, True], ids=["absolute-path", "symlink"])
def test_export_rejects_artifacts_outside_cache(tmp_path, symlink):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    cache, destination = tmp_path / "cache", tmp_path / "candidate"
    key, payload = cache_entry(cache, source)
    outside = tmp_path / "outside.xlsx"
    (cache / "original.xlsx").replace(outside)
    if symlink:
        (cache / "original.xlsx").symlink_to(outside)
    else:
        payload["excel_path"] = str(outside)
    store(cache, key, payload)

    with pytest.raises(ValueError, match="cache-local"):
        export(cache, source, destination)

    assert not destination.exists()


def test_export_cannot_write_into_its_source_cache(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    cache = tmp_path / "cache"
    key, payload = cache_entry(cache, source)
    store(cache, key, payload)

    with pytest.raises(ValueError, match="source cache"):
        export(cache, source, cache / "candidate")

    assert not (cache / "candidate").exists()


def test_export_rejects_a_record_with_changed_payload_identity(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    cache, destination = tmp_path / "cache", tmp_path / "candidate"
    key, payload = cache_entry(cache, source)
    store(cache, key, {**payload, "file_id": "another-source"})

    with pytest.raises(ValueError, match="identity"):
        export(cache, source, destination)

    assert not destination.exists()

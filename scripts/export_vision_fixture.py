"""Copy existing current-protocol OCR evidence; never invoke a provider or relabel legacy data."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from documents.excel.loader import load_excel_workbook
from documents.ocr.cache import VISION_CACHE_VERSION


def export(cache: Path, source: Path, destination: Path) -> int:
    cache, source, destination = cache.resolve(), source.resolve(strict=True), destination.resolve()
    if destination.is_relative_to(cache):
        raise ValueError("Evidence export must not write into the source cache")
    database = cache / "ocr_cache.db"
    if not database.is_file():
        return 0
    selected = []
    source_bytes = source.read_bytes()
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        for file_id, text in connection.execute("SELECT file_id, text FROM content_cache"):
            payload = json.loads(text)
            if not isinstance(payload, dict) or payload.get("vision_protocol") != VISION_CACHE_VERSION or "error" in payload:
                continue
            model = payload.get("vision_model")
            if not isinstance(model, str) or not model:
                continue
            expected = hashlib.md5(VISION_CACHE_VERSION.encode() + model.encode() + source_bytes).hexdigest()
            if file_id != expected:
                continue
            if payload.get("file_id") != file_id:
                raise ValueError("OCR evidence identity does not match its cache key")
            files = {}
            for field in ("excel_path", "html_path"):
                if field not in payload:
                    continue
                path = Path(payload[field])
                path = (path if path.is_absolute() else cache / path).resolve(strict=True)
                suffix = ".xlsx" if field == "excel_path" else ".html"
                if not path.is_relative_to(cache) or not path.is_file() or path.suffix.lower() != suffix:
                    raise ValueError("OCR evidence artifact is not a valid cache-local file")
                files[field] = path
            if "excel_path" not in files:
                raise ValueError("OCR evidence has no complete workbook")
            workbook = load_excel_workbook(str(files["excel_path"]))
            workbook.close()
            selected.append((file_id, model, files))
    if not selected:
        return 0
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "results").mkdir()
    with sqlite3.connect(destination / "ocr_cache.db") as connection:
        connection.execute("CREATE TABLE content_cache (file_id TEXT PRIMARY KEY, text TEXT)")
        connection.execute("CREATE TABLE url_cache (url_hash TEXT PRIMARY KEY, file_id TEXT)")
        for file_id, model, files in selected:
            payload = {"file_id": file_id, "vision_protocol": VISION_CACHE_VERSION, "vision_model": model}
            for field, path in files.items():
                relative = Path("results") / f"{file_id}{'.xlsx' if field == 'excel_path' else '.html'}"
                shutil.copyfile(path, destination / relative)
                payload[field] = str(relative)
            connection.execute("INSERT INTO content_cache VALUES (?, ?)", (file_id, json.dumps(payload, ensure_ascii=False)))
    return len(selected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    count = export(args.cache, args.source, args.destination)
    print(json.dumps({"protocol": VISION_CACHE_VERSION, "candidates": count, "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()

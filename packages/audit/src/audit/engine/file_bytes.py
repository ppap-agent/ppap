"""Read one frozen audit file without importing checker routing."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from documents.files import download_file_sync


def load_file_bytes(file_url: str) -> tuple[bytes, str]:
    parsed = urlparse(file_url)
    file_type = Path(unquote(parsed.path or file_url)).suffix.lower() or ".pdf"
    if parsed.scheme in ("http", "https"):
        file_content, _headers = download_file_sync(file_url, max_file_size=100 * 1024 * 1024)
        return file_content, file_type
    path_str = parsed.path if parsed.scheme == "file" else file_url
    local_path = Path(unquote(path_str))
    return local_path.read_bytes(), local_path.suffix.lower() or file_type

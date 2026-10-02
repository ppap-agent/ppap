"""Round-scoped original-file archive names. Does not own object storage."""

from __future__ import annotations

from pathlib import Path

from ppap_core.files import original_filename


def archive_member_names(entries: list[tuple[str, str]]) -> list[str]:
    """Stable ZIP names. Duplicate filenames keep the file_version prefix."""
    used: set[str] = set()
    names: list[str] = []
    for version_id, filename in entries:
        try:
            base = original_filename(filename)
        except ValueError:
            base = "file"
        name = base
        if name in used:
            stem = Path(base).stem or "file"
            suffix = Path(base).suffix
            name = f"{stem}-{version_id[:8]}{suffix}"
        used.add(name)
        names.append(name)
    return names

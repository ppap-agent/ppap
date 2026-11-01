"""Export only the bounded result; no directory copy crosses the VM boundary."""

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

from agent.execution.secure_files import read_regular

CHUNK_BYTES = 24576


def export_result(work: Path) -> bytes:
    try:
        return read_regular(work, ("result", "fault.json"), 1024)
    except FileNotFoundError:
        return read_regular(work, ("result", "bundle.json"), 16 * 1024 * 1024)


def export_chunk(work: Path, offset: int) -> bytes:
    raw = export_result(work)
    if offset < 0 or offset > len(raw):
        raise ValueError("Invalid result offset")
    chunk = {"total": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "offset": offset, "data": base64.b64encode(raw[offset : offset + CHUNK_BYTES]).decode("ascii")}
    return json.dumps(chunk, separators=(",", ":")).encode("ascii")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offset", type=int)
    args = parser.parse_args()
    work = Path("/work")
    sys.stdout.buffer.write(export_result(work) if args.offset is None else export_chunk(work, args.offset))


if __name__ == "__main__":
    main()

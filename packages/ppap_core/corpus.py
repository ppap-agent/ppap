"""Authorized corpus presence. Missing samples are NOT_PROVEN, never a silent pass."""
from __future__ import annotations

import os
from pathlib import Path


def corpus_status() -> dict[str, str]:
    root = (os.environ.get("PPAP_CORPUS_ROOT") or "").strip()
    sample = (os.environ.get("PPAP_SAMPLE") or "").strip()
    oracle = (os.environ.get("PPAP_ORACLE") or "").strip()
    if not root and not (sample and oracle):
        return {
            "state": "NOT_PROVEN",
            "reason": "authorized_corpus_not_configured",
        }
    if root:
        path = Path(root)
        if not path.is_dir():
            return {"state": "NOT_PROVEN", "reason": "corpus_root_missing"}
        return {"state": "configured", "reason": "local_corpus_root"}
    if not Path(sample).is_file() or not Path(oracle).is_file():
        return {"state": "NOT_PROVEN", "reason": "sample_or_oracle_missing"}
    return {"state": "configured", "reason": "sample_and_oracle"}

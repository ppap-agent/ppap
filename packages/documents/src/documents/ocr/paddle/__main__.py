"""Explicit local PaddleOCR evaluation; no production OCR routing or LLM calls."""

import argparse
import json
import os
from pathlib import Path

from .export import evaluate_pdf


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone PaddleOCR layout and seal evaluation")
    parser.add_argument("--pdf", required=True, type=Path, help="Local source PDF")
    parser.add_argument("--out-dir", required=True, type=Path, help="New directory for Excel and JSON evidence")
    parser.add_argument("--response-json", type=Path, help="Replay a saved Paddle response without network")
    args = parser.parse_args()

    response = json.loads(args.response_json.read_text(encoding="utf-8")) if args.response_json else None
    endpoint = os.environ.get("PADDLE_OCR_URL", "").strip() if response is None else None
    if response is None and not endpoint:
        parser.error("PADDLE_OCR_URL is required unless --response-json is provided")
    stats = evaluate_pdf(
        args.pdf,
        args.out_dir,
        response=response,
        endpoint=endpoint,
        token=os.environ.get("PADDLE_OCR_TOKEN") or os.environ.get("PADDLE_TOKEN", ""),
    )
    print(f"Paddle OCR: pages={stats.pages}, tables={stats.tables}, text_blocks={stats.text_blocks}, seals={stats.seals}")


if __name__ == "__main__":
    main()

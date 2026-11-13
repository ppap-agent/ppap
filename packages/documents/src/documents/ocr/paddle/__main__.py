"""Explicit local PaddleOCR evaluation; no production OCR routing or LLM calls."""

import argparse
import json
import os
from pathlib import Path

from .export import evaluate_pdf


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone PaddleOCR layout and seal evaluation")
    parser.add_argument("--pdf", required=True, type=Path, help="Local source PDF")
    parser.add_argument("--out-root", required=True, type=Path, help="Parent directory for unique PaddleOCR run outputs")
    parser.add_argument("--page", type=int, help="Original 1-based page number (required for live calls)")
    parser.add_argument("--response-json", type=Path, help="Replay a saved Paddle response without network")
    args = parser.parse_args()

    response = json.loads(args.response_json.read_text(encoding="utf-8")) if args.response_json else None
    endpoint = os.environ.get("PPAP_PADDLE_URL", "").strip() if response is None else None
    if response is None and not endpoint:
        parser.error("PPAP_PADDLE_URL is required unless --response-json is provided")
    if response is None and args.page is None:
        parser.error("--page is required for a live single-page request")
    stats = evaluate_pdf(
        args.pdf,
        args.out_root,
        response=response,
        page_number=args.page,
        endpoint=endpoint,
        token=os.environ.get("PPAP_PADDLE_TOKEN", ""),
    )
    print(
        f"Paddle OCR: status={stats.status} output_dir={stats.output_dir} "
        f"pages={stats.pages}, tables={stats.tables}, text_blocks={stats.text_blocks}, "
        f"seal_records={stats.seals} artifacts={','.join(stats.artifacts)}"
    )
    if stats.status == "error":
        parser.exit(1, "Paddle OCR did not produce usable evidence; inspect the saved diagnostics.\n")


if __name__ == "__main__":
    main()

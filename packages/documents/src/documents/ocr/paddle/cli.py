"""Standalone PaddleOCR evaluation CLI, no production integration."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import nullcontext
from pathlib import Path

from .client import downloaded_pdf
from .export import evaluate_pdf


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated PaddleOCR API evaluation (not connected to PPAP audit)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf", type=Path, help="Local source PDF")
    source.add_argument("--pdf-url", help="Explicit remote source PDF; no redirect following")
    parser.add_argument("--xlsx", type=Path, required=True, help="Destination XLSX")
    parser.add_argument("--html", type=Path, help="Optional escaped OCR HTML")
    parser.add_argument("--evidence-json", type=Path, help="Structured layout, table and seal evidence")
    parser.add_argument("--raw-json", type=Path, help="Save unmodified PaddleOCR result for replay")
    parser.add_argument("--seal-crop-dir", type=Path, help="Save cropped images of located seals")
    parser.add_argument("--non-table-dir", type=Path, help="Save reconstructed page images with table bands removed")
    parser.add_argument("--endpoint", help="Full PaddleOCR /layout-parsing URL (or PADDLE_OCR_URL)")
    parser.add_argument("--response-json", type=Path, help="Replay saved PaddleOCR API JSON without network")
    parser.add_argument("--join-continuations", action="store_true", help="Merge only repeated-header cross-page table candidates")
    args = parser.parse_args()
    if args.response_json and args.endpoint:
        parser.error("--response-json cannot be combined with --endpoint")
    endpoint = args.endpoint or (None if args.response_json else os.environ.get("PADDLE_OCR_URL"))
    if not args.response_json and not endpoint:
        parser.error("--endpoint or PADDLE_OCR_URL is required unless --response-json is provided")
    if args.pdf_url and args.response_json:
        parser.error("--response-json requires --pdf (offline replay never downloads)")
    response = json.loads(args.response_json.read_text(encoding="utf-8")) if args.response_json else None
    with downloaded_pdf(args.pdf_url) if args.pdf_url else nullcontext(args.pdf) as pdf_path:
        outcome = evaluate_pdf(
            pdf_path,
            xlsx_path=args.xlsx,
            html_path=args.html,
            evidence_json_path=args.evidence_json,
            raw_json_path=args.raw_json,
            seal_crop_dir=args.seal_crop_dir,
            non_table_dir=args.non_table_dir,
            endpoint=endpoint,
            token=os.environ.get("PADDLE_OCR_TOKEN") or os.environ.get("PADDLE_TOKEN", ""),
            response=response,
            join_continuations=args.join_continuations,
        )
    print(f"Paddle OCR completed: pages={outcome.pages}, tables={outcome.tables}, text_blocks={outcome.text_blocks}, seals={outcome.seals}")


if __name__ == "__main__":
    main()

"""Offline clause formalization for developers preparing a rule. It never reads or writes a product database.

    uv run --no-sync python -m product.formalization rules.json [--clause ID ...] [--output out.jsonl] [--no-model]

The input is a rules.json (`{"rules": [...]}`), a rule version (`{"body": {"rules": [...]}}`) or a list of
clauses. Each output line is one clause: the check points the model rewrote it into, the questions the text
leaves open, and a suggested wording when it has any. The model comes from the platform settings.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TextIO

from product.formalization.text import FormalizationError, accept_text_points, condition_questions, text_prompts


def clauses(document: Any) -> list[dict]:
    body = document.get("body", document) if isinstance(document, dict) else document
    rows = body.get("rules") if isinstance(body, dict) else body
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("input has no rules list")
    return rows


def formalize(clause: dict, ai: Callable[..., Any] | None) -> dict:
    clause_text = str(clause.get("rule_to_check") or "")
    found: dict[str, Any] = {"clause_id": str(clause.get("id") or ""), "clause_text": clause_text, "questions": condition_questions(clause_text)}
    if not clause_text.strip():
        return {**found, "state": "failed", "failure": "empty_clause"}
    if ai is None:
        return {**found, "state": "skipped"}
    system, user = text_prompts(clause_text)
    try:
        completed = ai("rule_formalization", instructions=system, prompt=user, max_tokens=32000, reasoning_effort="high", timeout=600)
        return {**found, "state": "formalized", **accept_text_points(completed.output, clause_text)}
    except FormalizationError as exc:
        return {**found, "state": "refused", "failure": exc.code, "detail": exc.detail}
    except Exception as exc:  # noqa: BLE001 — one clause failing never stops the others.
        return {**found, "state": "failed", "failure": getattr(exc, "code", type(exc).__name__)}


def run(rows: Iterable[dict], out: TextIO, ai: Callable[..., Any] | None) -> int:
    failed = 0
    for clause in rows:
        found = formalize(clause, ai)
        failed += found["state"] in {"refused", "failed"}
        out.write(json.dumps(found, ensure_ascii=False) + "\n")
        out.flush()
    return 1 if failed else 0


def main(argv: list[str] | None = None, *, ai: Callable[..., Any] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m product.formalization", description="Offline clause formalization; prints one JSON line per clause.")
    parser.add_argument("input", type=Path, help="rules.json, a rule version JSON, or a JSON list of clauses")
    parser.add_argument("--clause", action="append", default=[], help="only this clause id (repeatable)")
    parser.add_argument("--output", type=Path, help="write JSON lines here instead of stdout")
    parser.add_argument("--no-model", action="store_true", help="only the questions found without a model")
    args = parser.parse_args(argv)
    rows = clauses(json.loads(args.input.read_text(encoding="utf-8")))
    if args.clause:
        rows = [row for row in rows if str(row.get("id")) in set(args.clause)]
    if not args.no_model and ai is None:
        from documents.ai import run_json

        ai = run_json
    model = None if args.no_model else ai
    if args.output is None:
        return run(rows, sys.stdout, model)
    with args.output.open("w", encoding="utf-8") as out:
        return run(rows, out, model)


if __name__ == "__main__":
    raise SystemExit(main())

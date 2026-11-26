"""bench/deep-audit scoring, through its report command: the program's and the reviewed verdicts beside a revision's
reference judgments, over the benchmark's fixed rows."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "assets/uaes/rulebook/v1.0/candidates/m4/benchmark"


def _lines(name: str) -> list[dict]:
    return [json.loads(line) for line in (BENCHMARK / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def _ng_rows() -> tuple[str, list[dict], list[dict]]:
    """A benchmark batch and its expected rows whose reference is NG; the scoring reads the real reference."""
    reference = {(row["batch"], row["file"], row["clause"]): row["verdict"] for row in _lines("reference-judgments.jsonl")}
    by_batch: dict[str, list[dict]] = {}
    for row in _lines("expected-rows.jsonl"):
        by_batch.setdefault(row["batch"], []).append(row)
    batch, rows = next((batch, rows) for batch, rows in by_batch.items() if sum(reference[(r["batch"], r["file"], r["clause"])] == "NG" for r in rows) >= 3)
    return batch, rows, [row for row in rows if reference[(row["batch"], row["file"], row["clause"])] == "NG"]


def _finding(row: dict, program: str, deep: str, *, state: str = "completed", decision: str = "confirmed") -> dict:
    return {"kind": "finding", "batch": row["batch"], "source": row["file"], "clause": row["clause"], "status": deep, "program": program, "decision": decision, "target_state": state}


def _report(tmp_path, lines, meta=None, *, check=True):
    (tmp_path / "results.jsonl").write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps(meta or {"revision": "m4"}, ensure_ascii=False), encoding="utf-8")
    done = subprocess.run([sys.executable, str(ROOT / "bench" / "deep-audit" / "deep_eval.py"), "report", str(tmp_path)], check=check, capture_output=True, text=True)
    return json.loads((tmp_path / "summary.json").read_text(encoding="utf-8")) if check else done


def test_review_outcomes_are_set_against_the_reference_over_every_expected_row(tmp_path):
    batch, rows, ng = _ng_rows()
    lines = [
        _finding(ng[0], "NG", "NG"),
        _finding(ng[1], "UNABLE", "NG", decision="corrected"),
        _finding(ng[2], "NG", "PASS", decision="corrected"),
        {"kind": "deep", "batch": batch, "seconds": 10, "targets": [], "requests": []},
    ]
    summary = _report(tmp_path, lines)
    assert summary["rows"] == len(rows)
    assert (summary["program_agrees"], summary["deep_agrees"]) == (2, 2)
    assert (summary["fixed"], summary["broke"], summary["unable_settled_agrees"]) == (1, 1, 1)
    # Reviewing an NG into PASS against the reference is the direction deep mode must not take.
    assert summary["new_pass_against_reference"] == 1
    # Rows without a conclusion stay in the comparison.
    assert summary["categories"]["没有输出"]["deep"] == len(rows) - 3


def test_failed_rounds_are_counted_apart_and_their_spend_is_kept(tmp_path):
    batch, _rows, ng = _ng_rows()
    failed = next(row["batch"] for row in _lines("expected-rows.jsonl") if row["batch"] != batch)
    request = {"stage": "review", "target": "", "model": "grok-4.7", "effort": "medium", "input_tokens": 1000, "cached_tokens": 0, "output_tokens": 100, "reasoning_tokens": 0}
    lines = [
        _finding(ng[0], "NG", "NG", state="degraded"),
        {"kind": "deep", "batch": batch, "seconds": 5, "targets": [], "requests": []},
        {"kind": "deep", "batch": failed, "seconds": 9, "error": "DeepError: deep_budget_exhausted", "targets": [], "requests": [request]},
    ]
    summary = _report(tmp_path, lines)
    assert summary["rows_not_reviewed"] == 1
    # A failed round has no verdicts: its rows leave the comparison, its spend stays in the totals.
    assert summary["batches"] == 1 and summary["rows"] == sum(row["batch"] == batch for row in _lines("expected-rows.jsonl"))
    assert summary["failed_batch_rows"] == sum(row["batch"] == failed for row in _lines("expected-rows.jsonl"))
    assert summary["batch_errors"][failed]["requests"] == 1 and summary["usage"]["all"]["requests"] == 1
    assert summary["usage"]["failed_batches"]["usd"] > 0


def test_summary_names_the_revision_and_the_clauses_whose_text_differs(tmp_path):
    batch, _rows, ng = _ng_rows()
    meta = {"name": "trial", "revision": "m4", "commit": "abc", "clauses": {"revision": "m4", "leaves": {"uaes/U08": "x"}, "changed": [ng[0]["clause"]]}}
    _report(tmp_path, [_finding(ng[0], "NG", "NG"), {"kind": "deep", "batch": batch, "seconds": 3, "targets": [], "requests": []}], meta)
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "深度审核评估 trial" in text and "commit：abc" in text and "对照修订 m4" in text and ng[0]["clause"] in text


def test_a_revision_without_a_benchmark_is_refused(tmp_path):
    done = _report(tmp_path, [], {"revision": "m99"}, check=False)
    assert done.returncode != 0 and "修订 m99 没有基准" in done.stderr

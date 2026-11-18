"""deep-eval scoring, through its report command: what counts as right, fixed, broken and an unsafe PASS."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _row(truth, program, deep, *, state="completed", decision=None):
    return {"case": "c", "file": "f.xlsx", "clause": f"{truth}{program}{deep}", "truth": truth, "program": program, "deep": deep, "target_state": state, "target_reason": "", "decision": decision}


def _report(tmp_path, lines, meta=None) -> dict:
    (tmp_path / "cases.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    if meta is not None:
        (tmp_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "deep-eval" / "deep_eval.py"), "report", str(tmp_path)], check=True, capture_output=True)
    return json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))


def test_review_outcomes_are_scored_against_the_human_truth(tmp_path):
    rows = [
        _row("NG", "NG", "NG", decision="confirmed"),
        _row("NG", "UNABLE", "NG", decision="corrected"),
        _row("PASS", "PASS", "NG", decision="corrected"),
        _row("NG", "UNABLE", "PASS", decision="corrected"),
        _row("PASS", "NG", "UNABLE", decision="corrected"),
    ]
    summary = _report(tmp_path, [{"case": "c", "seconds": 10, "rows": rows}])
    assert (summary["rows"], summary["program_right"], summary["deep_right"]) == (5, 2, 2)
    assert (summary["fixed"], summary["broke"], summary["wrong_to_wrong"]) == (1, 1, 2)
    # Only a changed verdict that becomes PASS against the truth is the unsafe direction.
    assert summary["new_pass_against_truth"] == 1
    assert summary["by_truth"]["NG"] == {"rows": 3, "program_right": 1, "deep_right": 2}


def test_unreviewed_targets_failures_and_errors_are_reported_not_hidden(tmp_path):
    lines = [
        {"case": "a", "seconds": 5, "deep_failure": "deep_timeout", "rows": [_row("NG", "NG", "NG", state="degraded")]},
        {"case": "b", "seconds": 1, "error": "OSError: missing", "rows": []},
    ]
    summary = _report(tmp_path, lines)
    assert summary["rows_not_reviewed"] == 1 and summary["deep_failures"] == {"deep_timeout": 1}
    assert summary["case_errors"] == {"b": "OSError: missing"}
    assert summary["seconds_per_case"]["max"] == 5


def test_report_writes_a_readable_summary(tmp_path):
    _report(tmp_path, [{"case": "c", "seconds": 3, "rows": [_row("NG", "UNABLE", "NG")]}], {"name": "trial", "commit": "abc"})
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "深度审核评估 trial" in text and "commit：abc" in text

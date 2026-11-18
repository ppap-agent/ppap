"""deep-eval scoring: what counts as right, fixed, broken and an unsafe PASS."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("deep_eval", ROOT / "deep-eval" / "deep_eval.py")
assert _spec is not None and _spec.loader is not None
deep_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deep_eval)


def _row(truth, program, deep, *, state="completed", decision=None):
    return {"case": "c", "file": "f.xlsx", "clause": f"{truth}{program}{deep}", "truth": truth, "program": program, "deep": deep, "target_state": state, "target_reason": "", "decision": decision}


def test_review_outcomes_are_scored_against_the_human_truth():
    rows = [
        _row("NG", "NG", "NG", decision="confirmed"),
        _row("NG", "UNABLE", "NG", decision="corrected"),
        _row("PASS", "PASS", "NG", decision="corrected"),
        _row("NG", "UNABLE", "PASS", decision="corrected"),
        _row("PASS", "NG", "UNABLE", decision="corrected"),
    ]
    summary = deep_eval.summarize([{"case": "c", "seconds": 10, "rows": rows}])
    assert (summary["rows"], summary["program_right"], summary["deep_right"]) == (5, 2, 2)
    assert (summary["fixed"], summary["broke"], summary["wrong_to_wrong"]) == (1, 1, 2)
    # Only a changed verdict that becomes PASS against the truth is the unsafe direction.
    assert summary["new_pass_against_truth"] == 1
    assert summary["by_truth"]["NG"] == {"rows": 3, "program_right": 1, "deep_right": 2}


def test_unreviewed_targets_failures_and_errors_are_reported_not_hidden():
    lines = [
        {"case": "a", "seconds": 5, "deep_failure": "deep_timeout", "rows": [_row("NG", "NG", "NG", state="degraded")]},
        {"case": "b", "seconds": 1, "error": "OSError: missing", "rows": []},
    ]
    summary = deep_eval.summarize(lines)
    assert summary["rows_not_reviewed"] == 1 and summary["deep_failures"] == {"deep_timeout": 1}
    assert summary["case_errors"] == {"b": "OSError: missing"}
    assert summary["seconds_per_case"]["max"] == 5


def test_report_writes_both_summaries(tmp_path):
    (tmp_path / "cases.jsonl").write_text(json.dumps({"case": "c", "seconds": 3, "rows": [_row("NG", "UNABLE", "NG")]}) + "\n", encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"name": "trial", "commit": "abc"}), encoding="utf-8")
    deep_eval.report(tmp_path)
    assert json.loads((tmp_path / "summary.json").read_text())["fixed"] == 1
    text = (tmp_path / "summary.md").read_text()
    assert "深度审核评估 trial" in text and "commit：abc" in text

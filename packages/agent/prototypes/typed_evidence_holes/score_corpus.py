"""Score research artifacts against the existing human corpus, without model calls.

Only completed artifact contents are scored here. A matching result is not proof
of binding correctness, product acceptance, or successful agent termination.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from audit.secure_files import reject_constant, unique_object
from corpus_contracts.acceptance_report import write_acceptance_report
from corpus_contracts.product_round import MATCH, compare_file

ROOT = Path(__file__).resolve().parents[4]
STATUSES = {"PASS", "NG", "UNABLE", "ERR", "NA"}


@dataclass(frozen=True)
class OracleCase:
    file_name: str
    file_path: Path
    file_seq: str
    expected: dict[int, str]
    bundle: str = ""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases(corpus: str, assets_root: Path, doc_key: str | None) -> tuple[list[OracleCase], dict[str, Any]]:
    """Keep file mapping, normalization and expectations with their current owners."""
    if corpus == "v2":
        from corpus_contracts.v2_assets_shared import build_cases, parse_doc_key

        oracle = assets_root / "全量测试 - 4.8文件测试反馈（云端） - 4.8（云端）.csv"
        source = build_cases(dataset_root=assets_root / "v2.0plus", expected_csv=oracle, include_unlabeled=False)
        cases = [OracleCase(case.file_name, case.file_path, str(parse_doc_key(case.file_name)), {index: item.status for index, item in case.expected.items()}) for case in source]
    elif corpus == "v3":
        from corpus_contracts.poc_oracle import load_poc_cases

        oracle = assets_root / "poc测试" / "poc-测试数据统计表.xlsx"
        poc_cases = load_poc_cases(dataset_root=assets_root / "poc测试" / "POC测试样本", expected_xlsx=oracle)
        cases = [OracleCase(case.file_name, case.file_path, case.file_seq, {index: item.status for index, item in case.expected.items()}, case.bundle_id) for case in poc_cases]
    else:
        raise ValueError("Unknown corpus")
    metadata = {
        "corpus": corpus,
        "oracle_path": str(oracle.resolve()),
        "oracle_sha256": sha256(oracle),
        "corpus_total": {"cases": len(cases), "rules": sum(len(case.expected) for case in cases)},
        "doc_key": doc_key,
    }
    selected = [case for case in cases if doc_key is None or case.file_seq == doc_key]
    if not selected:
        raise ValueError("The selected corpus scope has no cases")
    return selected, metadata


def _checks(raw: Any, expected: dict[int, str]) -> dict[int, str]:
    if not isinstance(raw, list):
        raise ValueError("Artifact checks must be a list")
    found: dict[int, str] = {}
    for check in raw:
        if not isinstance(check, dict) or not isinstance(check.get("rule_id"), str) or not re.fullmatch(r"R[1-9][0-9]*", check["rule_id"]):
            raise ValueError("Artifact rule IDs must be canonical R1, R2, ...")
        index = int(check["rule_id"][1:])
        if index in found:
            raise ValueError("Duplicate artifact rule ID")
        if index not in expected:
            raise ValueError("Artifact rule is outside the selected human expectation scope")
        status = check.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError("Unknown artifact status")
        found[index] = status
    return found


def score_artifacts(paths: list[Path], cases: list[OracleCase]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Adapt validated IDs to the existing strict comparator, without exceptions."""
    if not paths or not cases:
        raise ValueError("At least one artifact and one oracle case are required")
    by_hash: dict[str, list[OracleCase]] = {}
    for case in cases:
        if not case.expected or any(type(index) is not int or index < 1 or value not in {"PASS", "NG"} for index, value in case.expected.items()):
            raise ValueError("Invalid human expectation scope")
        by_hash.setdefault(sha256(case.file_path), []).append(case)
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    artifact_refs: list[dict[str, str]] = []
    present = expected_submitted = 0
    for path in paths:
        source = path.read_bytes()
        raw = json.loads(source, object_pairs_hook=unique_object, parse_constant=reject_constant)
        if not isinstance(raw, dict):
            raise ValueError("Artifact must be a JSON object")
        digest = raw.get("document_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Artifact must identify its original document SHA-256")
        matches = by_hash.get(digest, [])
        if len(matches) != 1:
            raise ValueError("Artifact document hash is unknown or ambiguous in the selected corpus")
        if digest in seen:
            raise ValueError("More than one artifact was submitted for the same corpus case")
        case = matches[0]
        actual = _checks(raw.get("checks"), case.expected)
        seen.add(digest)
        present += len(actual)
        expected_submitted += len(case.expected)
        artifact_refs.append({"path": str(path.resolve()), "sha256": hashlib.sha256(source).hexdigest(), "document_sha256": digest})
        result = {"coverage": {"rule_items": [{"file_version_id": digest, "id": str(index), "status": status, "coverage": "executed"} for index, status in actual.items()]}}
        verdicts = compare_file(file_name=case.file_name, version_id=digest, expected=case.expected, result=result, setup_error=None, expected_coverage="executed")
        rows.extend(
            {
                "bundle": case.bundle,
                "file_seq": case.file_seq,
                "file_name": case.file_name,
                "document_sha256": digest,
                "rule_index": item.rule_index,
                "expected_status": item.expected,
                "actual_status": item.actual,
                "kind": item.kind,
            }
            for item in verdicts
        )
    total_rules = sum(len(case.expected) for case in cases)
    unmatched = [case for digest, entries in by_hash.items() if digest not in seen for case in entries]
    matched = sum(row["kind"] == MATCH for row in rows)
    metrics = {
        "scope": {"cases": len(cases), "expected_rules": total_rules},
        "submitted": {"cases": len(seen), "expected_rules": expected_submitted, "present_rules": present},
        "not_executed": {"cases": len(unmatched), "expected_rules": sum(len(case.expected) for case in unmatched)},
        "rule_coverage": present / total_rules,
        "strict_full_scope_match_rate": matched / total_rules,
        "counts": {
            "match": matched,
            "false_pass": sum(row["actual_status"] == "PASS" and row["expected_status"] == "NG" for row in rows),
            "false_ng": sum(row["actual_status"] == "NG" and row["expected_status"] == "PASS" for row in rows),
            "unable": sum(row["actual_status"] == "UNABLE" for row in rows),
            "err": sum(row["actual_status"] == "ERR" for row in rows),
            "na": sum(row["actual_status"] == "NA" for row in rows),
            "missing_rule": sum(row["kind"] == "missing_rule" for row in rows),
        },
        "unexecuted_cases": [{"bundle": case.bundle, "file_name": case.file_name, "expected_rules": len(case.expected)} for case in unmatched],
        "artifacts": artifact_refs,
    }
    return rows, metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path, nargs="+")
    parser.add_argument("--corpus", choices=["v2", "v3"], required=True)
    parser.add_argument("--doc-key", help="Select one complete document-type scope, including all formats")
    parser.add_argument("--assets-root", type=Path, default=ROOT / "assets")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output must not exist; preserve prior scoring records")
    try:
        cases, source = load_cases(args.corpus, args.assets_root, args.doc_key)
        rows, metrics = score_artifacts(args.artifacts, cases)
    except (OSError, ValueError, AssertionError) as error:
        parser.error(str(error))
    write_acceptance_report(
        args.output,
        rows,
        run=source | metrics | {"kind": "research_artifact_score", "baseline_exceptions_applied": False, "product_acceptance": False, "binding_gold": False, "agent_termination_checked": False},
    )
    print(json.dumps({"output": str(args.output.resolve()), **metrics, "strict_submitted_match_rate": metrics["counts"]["match"] / metrics["submitted"]["expected_rules"]}, ensure_ascii=False))
    # A subset is not a full-corpus pass, even when every submitted row matches.
    return int(metrics["counts"]["match"] != metrics["scope"]["expected_rules"])


if __name__ == "__main__":
    raise SystemExit(main())

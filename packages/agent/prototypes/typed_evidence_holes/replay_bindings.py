"""Recompute completed source bindings without another model call or overwriting history."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import bound_document
from agent.execution.secure_files import reject_constant, unique_object
from audit.engine.checkers import dimension_measurement_policy
from bound_document import RULES_SHA256, DocumentBinding, evaluate
from inspect_original import inspect_original
from inspect_pdf import inspect_pdf


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_bytes(), object_pairs_hook=unique_object, parse_constant=reject_constant)


def replay(case: Path, output: Path) -> dict:
    summary = read(case / "summary.json")
    usage = summary.get("usage", {})
    if (
        summary.get("run_completed") is not True
        or summary.get("returncode") != 0
        or summary.get("source_unchanged") is not True
        or (summary.get("terminal") or {}).get("is_error", True)
        or usage.get("uncertain", True)
        or usage.get("in_flight", True)
        or summary.get("validation", {}).get("errors") != []
    ):
        raise ValueError("Replay requires a completed, settled, validated original run")
    originals = list((case / "inputs").glob("document.*"))
    if len(originals) != 1:
        raise ValueError("Replay requires exactly one frozen original document")
    document, rules = originals[0], case / "inputs/rules.docx"
    artifact_path, snapshot_path = case / "work/execution.json", case / "work/document_read/snapshot.json"
    previous, frozen = read(artifact_path), read(snapshot_path)
    if (
        sha(document) != previous.get("document_sha256")
        or sha(document) != summary.get("document_sha256")
        or sha(document) != frozen.get("source", {}).get("sha256")
        or sha(rules) != RULES_SHA256
        or sha(rules) != previous.get("rules_sha256")
        or sha(rules) != summary.get("rules_sha256")
    ):
        raise ValueError("Original document or rules fingerprint mismatch")
    binding = DocumentBinding.model_validate(previous["binding"])
    output.mkdir(mode=0o700)
    fresh = (inspect_pdf if document.suffix == ".pdf" else inspect_original)(document, output / "document_read")
    # The model saw the old snapshot. A new parser must not silently change the
    # meaning of its selected addresses during host-only policy replay.
    for key in ("source", "tables", "drawing_anchors", "table_image_references"):
        if fresh.get(key) != frozen.get(key):
            raise ValueError(f"Frozen source observations differ on {key}; binding replay is unsafe")
    result = evaluate(binding, fresh, rules_sha256=RULES_SHA256)
    result["execution_kind"] = "deterministic_replay"
    result["replay"] = {
        "source_execution": str(artifact_path.resolve()),
        "source_execution_sha256": sha(artifact_path),
        "source_snapshot_sha256": sha(snapshot_path),
        "evaluator_sha256": sha(Path(bound_document.__file__)),
        "measurement_policy_sha256": sha(Path(dimension_measurement_policy.__file__)),
        "additional_model_requests": 0,
    }
    shutil.copytree(case / "inputs", output / "inputs")
    (output / "execution.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="+", type=Path, help="Completed original case directories")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or len({case.name for case in args.cases}) != len(args.cases):
        parser.error("Require a new output directory and unique case names")
    args.output.mkdir(mode=0o700, parents=True)
    for module in (bound_document, dimension_measurement_policy):
        assert module.__file__ is not None
        module_path = Path(module.__file__)
        shutil.copy2(module_path, args.output / module_path.name)
    shutil.copy2(__file__, args.output / "replay_bindings.py")
    for case in args.cases:
        try:
            result = replay(case, args.output / case.name)
        except (OSError, ValueError, KeyError) as error:
            parser.error(str(error))
        print(json.dumps({"case": case.name, "model_requests": 0, "statuses": {item["rule_id"]: item["status"] for item in result["checks"]}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run candidate checker programs on real files and show what they conclude (docs/rules.md, step 7).

A candidate is a checker leaf written against wording that is not frozen or not clarified yet. It is kept outside the
installed tree, so nothing seeds it as an official Suite or lets it near a customer's audit:

    assets/<customer>/rulebook/<version>/candidates/m<revision>/<customer>/<leaf>/{rules.json, checker.py, resources.py}

It replaces the installed leaf of the same name for this run only; files go through the product path (recognition,
precheck, the frozen run) exactly as a sample batch does. Give one leaf, or the customer directory to replace every
leaf in it at once.

    uv run --no-sync python -m corpus_contracts.candidate_run <leaf or customer directory> --part <part directory under samples>
    uv run --no-sync python -m corpus_contracts.candidate_run <leaf or customer directory> --file <file> ... [--reference <file> ...]
    uv run --no-sync python -m corpus_contracts.candidate_run <customer directory> --benchmark <benchmark directory> [--batch <name> ...]

A benchmark (``batches.jsonl`` and ``file-inventory.jsonl``) is resubmitted batch by batch from its originals and their
submission roles alone; every submitted file reports. Its expected types and roles and its reference judgments only
check the output afterwards (``corpus_contracts.benchmark_diff``).

Models are the platform's, from the environment (PPAP_MODEL_TEXT_*, PPAP_MODEL_VISION_*); without them a reading that
needs a model comes back UNABLE. Results are shown, never recorded as validation or proof. Each submitted file recognition
left out of the audit is printed to stderr with its state and notices (why it was not matched).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from audit.standard_suites import _load_leaf, predefined_directory_suites
from documents.model_access import model_access_scope, platform_model_access

from corpus_contracts.oracle import REPO_ROOT
from corpus_contracts.uaes_samples import Batch, batches, run_batch


def candidate(directory: Path):
    """The leaf at `directory`, read and checked as an installed leaf is; it must name an installed leaf to replace. Only this
    leaf is read: other candidates and the run results kept beside them are not part of the run."""
    directory = directory.resolve()
    path = f"{directory.parent.name}/{directory.name}"
    try:
        suite = _load_leaf(directory.parent.parent, directory.parent.name, directory)
    except ValueError as exc:
        raise SystemExit(f"不是检查程序目录：{directory}（{exc}）") from None
    installed = {suite.source_path: suite for suite in predefined_directory_suites()}
    if path not in installed:
        raise SystemExit(f"没有同名的已安装检查程序可以替换：{path}")
    return suite, installed


def candidates(directory: Path) -> tuple[list[Any], dict[str, Any]]:
    """The candidate leaf at `directory`, or every leaf of the customer directory `directory`, with the installed leaves."""
    if (directory / "rules.json").is_file():
        suite, installed = candidate(directory)
        return [suite], installed
    leaves = sorted(path for path in directory.iterdir() if path.is_dir() and not path.name.startswith(("_", ".")))
    if not leaves:
        raise SystemExit(f"不是检查程序目录：{directory}")
    found = [candidate(leaf) for leaf in leaves]
    return [suite for suite, _ in found], found[0][1]


def benchmark_batches(directory: Path) -> list[tuple[str, Batch]]:
    """Each benchmark batch as it was submitted: the originals, checked against their recorded digests, and their submission
    roles. Every submitted file reports. The batch runs under an opaque name; its directory-derived name, the expected types
    and roles never reach recognition or a checker."""

    def lines(name: str) -> list[dict[str, Any]]:
        return [json.loads(line) for line in (directory / name).read_text(encoding="utf-8").splitlines() if line.strip()]

    inventory = lines("file-inventory.jsonl")
    out = []
    for index, batch in enumerate(lines("batches.jsonl"), 1):
        submitted: list[Path] = []
        references: list[Path] = []
        for row in (row for row in inventory if row["batch"] == batch["batch"]):
            path = REPO_ROOT / row["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                raise SystemExit(f"原件与基准登记的摘要不一致：{row['file']}")
            if row["submission_role"] not in {"submitted", "reference"}:
                raise SystemExit(f"未知的提交角色：{row['submission_role']}（{row['file']}）")
            (submitted if row["submission_role"] == "submitted" else references).append(path)
        if not submitted:
            raise SystemExit(f"基准批次没有提交文件：{batch['batch']}")
        out.append((batch["batch"], Batch(part=f"benchmark-{index:02d}", files=tuple(submitted), references=tuple(references))))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m corpus_contracts.candidate_run", description=__doc__.split("\n\n")[0])
    parser.add_argument("leaf", type=Path, help="候选检查程序目录，或客户目录（换上其中全部候选）")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--part", help="assets/uaes/samples 下的零件目录，按样本批次整批提交")
    where.add_argument("--file", type=Path, action="append", help="要审核的文件，可重复")
    where.add_argument("--benchmark", type=Path, help="基准目录，按其批次与提交角色逐批提交")
    parser.add_argument("--reference", type=Path, action="append", default=[], help="同批提交、只作比对的文件，可重复")
    parser.add_argument("--batch", action="append", default=[], help="只跑基准里的这些批次，可重复")
    parser.add_argument("--json", action="store_true", help="逐行输出 JSON")
    args = parser.parse_args(argv)

    suites, installed = candidates(args.leaf.resolve())
    replaced = {**installed, **{suite.source_path: suite for suite in suites}}
    if args.benchmark:
        runs = benchmark_batches(args.benchmark.resolve())
        unknown = set(args.batch) - {name for name, _ in runs}
        if unknown:
            raise SystemExit(f"基准里没有这些批次：{'、'.join(sorted(unknown))}")
        runs = [(name, batch) for name, batch in runs if not args.batch or name in args.batch]
    elif args.part:
        batch = next((batch for batch in batches() if batch.label == args.part), None)
        if batch is None:
            raise SystemExit(f"没有这个样本批次：{args.part}")
        runs = [(batch.label, batch)]
    else:
        batch = Batch(part="candidate", files=tuple(path.resolve() for path in args.file), references=tuple(path.resolve() for path in args.reference))
        runs = [(batch.label, batch)]
    clauses = {row["id"] for suite in suites for row in json.loads(suite.files["rules.json"])["rules"]}
    for name, batch in runs:
        # The audit runs under the platform model, as a frozen run does; recognition reads it from the environment either way.
        with model_access_scope(platform_model_access):
            found = [{**row, "batch": name} for row in run_batch(batch, replaced)]
        rows = [row for row in found if row["kind"] == "finding" and row["clause"] in clauses]
        # Files recognition left out of the audit go to stderr, so stdout stays the findings alone.
        for row in found:
            if row["kind"] == "recognition" and row["state"] not in {"matched", "reference"}:
                print(f"未进入审核：{row['source']}\t{row['state']}\t类型 {row['document_type'] or '未知'}\t{'、'.join(row['notices']) or '无提示'}", file=sys.stderr)
        if args.benchmark:
            # A benchmark run keeps how each file was taken and how long it was checked, to read against the expected rows.
            rows += [row for row in found if row["kind"] in {"recognition", "timing"}]
        if args.json:
            sys.stdout.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
            sys.stdout.flush()
        else:
            for row in rows:
                if row["kind"] == "finding":
                    print(f"{row['source']}\t{row['clause']}\t{row['status']}\t{row['reason'][:200]}")
        if not any(row["kind"] == "finding" for row in rows):
            print(f"{name}：候选检查程序没有对这批文件给出结论，没有文件识别为它们的文件类型。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

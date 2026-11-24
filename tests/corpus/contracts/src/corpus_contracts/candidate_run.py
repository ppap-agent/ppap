"""Run a candidate checker program on real files and show what it concludes (docs/rules.md, step 7).

A candidate is a checker leaf written against wording that is not frozen or not clarified yet. It is kept outside the
installed tree, so nothing seeds it as an official Suite or lets it near a customer's audit:

    assets/<customer>/rulebook/<version>/candidates/<customer>/<leaf>/{rules.json, checker.py, resources.py}

It replaces the installed leaf of the same name for this run only; files go through the product path (recognition,
precheck, the frozen run) exactly as a sample batch does.

    uv run --no-sync python -m corpus_contracts.candidate_run <leaf directory> --part <part directory under samples>
    uv run --no-sync python -m corpus_contracts.candidate_run <leaf directory> --file <file> ... [--reference <file> ...]

Models are the platform's, from the environment (PPAP_MODEL_TEXT_*, PPAP_MODEL_VISION_*); without them a reading that
needs a model comes back UNABLE. Results are shown, never recorded as validation or proof.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from audit.standard_suites import _load_leaf, predefined_directory_suites
from documents.model_access import model_access_scope, platform_model_access

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m corpus_contracts.candidate_run", description=__doc__.split("\n\n")[0])
    parser.add_argument("leaf", type=Path, help="候选检查程序目录")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--part", help="assets/uaes/samples 下的零件目录，按样本批次整批提交")
    where.add_argument("--file", type=Path, action="append", help="要审核的文件，可重复")
    parser.add_argument("--reference", type=Path, action="append", default=[], help="同批提交、只作比对的文件，可重复")
    parser.add_argument("--json", action="store_true", help="逐行输出 JSON")
    args = parser.parse_args(argv)

    suite, installed = candidate(args.leaf)
    if args.part:
        batch = next((batch for batch in batches() if batch.label == args.part), None)
        if batch is None:
            raise SystemExit(f"没有这个样本批次：{args.part}")
    else:
        batch = Batch(part="candidate", files=tuple(path.resolve() for path in args.file), references=tuple(path.resolve() for path in args.reference))
    clauses = {row["id"] for row in json.loads(suite.files["rules.json"])["rules"]}
    # The audit runs under the platform model, as a frozen run does; recognition reads it from the environment either way.
    with model_access_scope(platform_model_access):
        found = run_batch(batch, {**installed, suite.source_path: suite})
    rows = [row for row in found if row["kind"] == "finding" and row["clause"] in clauses]
    if args.json:
        sys.stdout.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    else:
        for row in rows:
            print(f"{row['source']}\t{row['clause']}\t{row['status']}\t{row['reason'][:200]}")
    if not rows:
        print(f"候选检查程序 {suite.source_path} 没有对这批文件给出结论：没有文件识别为它的文件类型。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

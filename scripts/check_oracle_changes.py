"""Keep human corpus truth changes separate from implementation changes."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
from pathlib import Path

ORACLE = "assets/corpus/oracle.csv"
IDENTITY = ("数据集", "组合ID", "文件ID", "条款ID")


def truth(text: str) -> dict[tuple[str, ...], str]:
    if not text:
        return {}
    rows = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not set((*IDENTITY, "人工真值")) <= set(rows.fieldnames or ()):
        raise ValueError("invalid_oracle_columns")
    result = {}
    for row in rows:
        key = tuple(row[column] for column in IDENTITY)
        if key in result:
            raise ValueError("duplicate_oracle_identity")
        result[key] = row["人工真值"]
    return result


def verify_change(before: str, after: str, paths: list[str], message: str) -> None:
    if truth(before) == truth(after):
        return
    if any(path != ORACLE and not path.startswith("docs/") for path in paths):
        raise ValueError("Human oracle truth must change in a separate commit without implementation, fixtures, or expected-output changes.")
    if not re.search(r"(?m)^Oracle-Basis:\s*\S.{9,}$", message):
        raise ValueError("A human truth change needs an Oracle-Basis: paragraph explaining the independent human evidence.")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8")


def verify_range(base: str, head: str) -> None:
    if any(not re.fullmatch(r"[a-fA-F0-9]{40,64}", value) for value in (base, head)):
        raise ValueError("invalid_revision_identity")
    # Inspect author commits and merge conflict resolutions. A merge which
    # simply retains one parent's human truth does not re-author that truth.
    for commit in git("rev-list", "--reverse", f"{base}..{head}").splitlines():
        parents = git("rev-list", "--parents", "-n", "1", commit).split()[1:]
        if not parents:
            continue
        paths = git("diff", "--name-only", parents[0], commit).splitlines()
        if ORACLE not in paths:
            continue
        before = git("show", f"{parents[0]}:{ORACLE}")
        after = git("show", f"{commit}:{ORACLE}")
        if len(parents) > 1 and any(truth(git("show", f"{parent}:{ORACLE}")) == truth(after) for parent in parents):
            continue
        verify_change(before, after, paths, git("show", "-s", "--format=%B", commit))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    args = parser.parse_args()
    event = json.loads(args.event.read_text())
    pr = event.get("pull_request")
    if pr:
        base, head = pr["base"]["sha"], pr["head"]["sha"]
    else:
        head = event.get("after") or git("rev-parse", "HEAD").strip()
        base = event.get("before")
        if not base or not base.strip("0"):
            base = git("rev-parse", f"{head}^").strip()
    verify_range(base, head)


if __name__ == "__main__":
    main()

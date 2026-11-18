"""Keep human corpus truth changes separate from implementation changes.

Each customer keeps its own tables (assets/corpus/<customer>-cases.csv, -oracle.csv and
-reviewed-rules.json). A commit is judged by what those tables say together, so moving rows
between files changes nothing, while any changed truth, file binding or reviewed rule needs
its own commit with an Oracle-Basis paragraph.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
from pathlib import Path

CUSTOMERS = ("li", "nio", "uaes")
ORACLES = tuple(f"assets/corpus/{customer}-oracle.csv" for customer in CUSTOMERS)
CASES = tuple(f"assets/corpus/{customer}-cases.csv" for customer in CUSTOMERS)
REVIEWED_RULES = tuple(f"assets/corpus/{customer}-reviewed-rules.json" for customer in CUSTOMERS)
# The single shared tables before each customer got its own, still read in older commits.
LEGACY = ("assets/corpus/oracle.csv", "assets/corpus/cases.csv", "assets/corpus/reviewed-rules.json")
HUMAN = frozenset((*ORACLES, *CASES, *REVIEWED_RULES, *LEGACY))
IDENTITY = ("数据集", "组合ID", "文件ID", "条款ID")


def _merge(parts: list[dict], duplicate: str) -> dict:
    merged: dict = {}
    for part in parts:
        if set(part) & set(merged):
            raise ValueError(duplicate)
        merged.update(part)
    return merged


def truth(text: str) -> dict[tuple[str, ...], str]:
    if not text:
        return {}
    rows = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not set((*IDENTITY, "人工真值")) <= set(rows.fieldnames or ()):
        raise ValueError("invalid_oracle_columns")
    result = {}
    for row in rows:
        key = tuple(row[column] for column in IDENTITY)
        if key in result:
            raise ValueError("duplicate_oracle_identity")
        result[key] = row["人工真值"]
    return result


def case_bindings(text: str) -> dict:
    """Keep each answer attached to its reviewed original and exact Suite source."""
    if not text:
        return {}
    rows = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    identity = IDENTITY[:3]
    fields = ("原件", "提交文件名", "角色", "Suite版本", "Checker组", "Binding")
    if not set((*identity, *fields)) <= set(rows.fieldnames or ()):
        raise ValueError("invalid_case_columns")
    result = {}
    for row in rows:
        key = tuple(row[column] for column in identity)
        if key in result:
            raise ValueError("duplicate_case_identity")
        result[key] = (*[row[column] for column in fields[:-1]], tuple(sorted(row["Binding"].split(";"))))
    return result


def reviewed(text: str) -> dict:
    return json.loads(text) if text else {}


def human_state(read) -> tuple[dict, dict, dict]:
    """Truth, file bindings and reviewed rules of every human table at one revision."""
    oracles = [truth(read(path)) for path in (*ORACLES, LEGACY[0])]
    cases = [case_bindings(read(path)) for path in (*CASES, LEGACY[1])]
    rules = [reviewed(read(path)) for path in (*REVIEWED_RULES, LEGACY[2])]
    return _merge(oracles, "duplicate_oracle_identity"), _merge(cases, "duplicate_case_identity"), _merge(rules, "duplicate_reviewed_suite")


def require_human_review(paths: list[str], message: str) -> None:
    if any(path not in HUMAN and not path.startswith("docs/") for path in paths):
        raise ValueError("Human oracle truth must change in a separate commit without implementation, fixtures, or expected-output changes.")
    if not re.search(r"(?m)^Oracle-Basis:\s*\S.{9,}$", message):
        raise ValueError("A human truth change needs an Oracle-Basis: paragraph explaining the independent human evidence.")


def verify_change(before: tuple[dict, dict, dict], after: tuple[dict, dict, dict], paths: list[str], message: str) -> None:
    if before != after:
        require_human_review(paths, message)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8")


def content(revision: str, path: str) -> str:
    if not git("ls-tree", "--name-only", revision, "--", path).strip():
        return ""
    return git("show", f"{revision}:{path}")


def state_at(revision: str) -> tuple[dict, dict, dict]:
    return human_state(lambda path: content(revision, path))


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
        if not HUMAN & set(paths):
            continue
        after = state_at(commit)
        if len(parents) > 1 and any(state_at(parent) == after for parent in parents):
            continue
        verify_change(state_at(parents[0]), after, paths, git("show", "-s", "--format=%B", commit))


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

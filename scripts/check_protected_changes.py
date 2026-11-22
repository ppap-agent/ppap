"""Changes no commit makes silently.

- A numbered migration already in the repository is not changed, renamed or deleted: a database that ran it never
  runs the new text's effect (``CREATE ... IF NOT EXISTS``), so a change goes into a new migration.
- Each company's checkers and readers, the prechecks, customer assets and the human corpus are not deleted or emptied
  (moving one keeps it).
- No added line carries a Feishu open_id/union_id, a private key or an access key.

A commit that must do one of the first two anyway (an edit that only keeps a migration safe to re-run, a customer's
new version replacing the old file) says why in a ``Protected-Change:`` paragraph of its message. Secrets have no
such exception.

pre-commit runs it on the staged commit at the commit-msg stage; CI runs it on every commit of the pushed range.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

MIGRATION = re.compile(r"migrations/\d{4}_[^/]+\.sql")
PROTECTED = (
    *(f"packages/audit/src/audit/{part}/{company}/" for part in ("checkers", "checker_lib") for company in ("uaes", "li", "nio")),
    "packages/product/src/product/precheck",
    "assets/uaes/",
    "assets/li/",
    "assets/nio/",
    "assets/corpus/",
)
SECRETS = {
    "飞书用户标识": re.compile(r"\bo[un]_[0-9a-f]{32}\b"),
    "私钥": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "模型 API Key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "云访问密钥": re.compile(r"\bAKIA[0-9A-Z]{16}\b|\bAKID[0-9A-Za-z]{32}\b"),
    "GitHub Token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b"),
}


def protected(path: str) -> bool:
    return path.startswith(PROTECTED)


def problems(changes: list[tuple[str, str, str]], emptied: set[str], added: list[tuple[str, str]], message: str) -> list[str]:
    """``changes`` are (status letter, old path, new path) as ``git diff --name-status -M`` gives them."""
    found = []
    if re.search(r"^Protected-Change:\s*\S", message, re.M) is None:
        for status, old, _ in changes:
            if status != "A" and MIGRATION.fullmatch(old):
                found.append(f"{old}：已有迁移不能修改、改名或删除，请新增迁移")
            if status == "D" and protected(old):
                found.append(f"{old}：受保护资产不能删除")
        found += [f"{path}：受保护资产不能清空" for path in sorted(emptied)]
    for path, line in added:
        found += [f"{path}：新增内容含{name}" for name, pattern in SECRETS.items() if pattern.search(line)]
    return found


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def diff(spec: list[str], blob: str) -> tuple[list[tuple[str, str, str]], set[str], list[tuple[str, str]]]:
    """``blob`` prefixes a path to name its new content: ``:`` for the index, ``<commit>:`` for a commit."""
    changes = []
    fields = git("diff", "--name-status", "-M", "-z", *spec).split("\0")
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index][0]
        if status in "RC":
            old, new, index = fields[index + 1], fields[index + 2], index + 3
        else:
            old = new = fields[index + 1]
            index += 2
        changes.append((status, old, new))
    emptied = {new for status, _, new in changes if status == "M" and protected(new) and git("cat-file", "-s", blob + new).strip() == "0"}
    added, path = [], ""
    for line in git("diff", "-U0", "--no-color", "--no-ext-diff", *spec).splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("+") and path:
            added.append((path, line[1:]))
    return changes, emptied, added


def check(spec: list[str], blob: str, message: str, label: str) -> list[str]:
    return [f"{label}{problem}" for problem in problems(*diff(spec, blob), message)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--staged", type=Path, metavar="COMMIT_MSG_FILE", help="the staged commit and its message file")
    source.add_argument("--event", type=Path, help="GitHub event payload: every commit of the pushed range or pull request")
    args = parser.parse_args(argv)
    if args.staged:
        found = check(["--cached"], ":", args.staged.read_text(encoding="utf-8"), "")
    else:
        event = json.loads(args.event.read_text())
        pr = event.get("pull_request")
        head = pr["head"]["sha"] if pr else event.get("after") or git("rev-parse", "HEAD").strip()
        base = pr["base"]["sha"] if pr else event.get("before")
        if not base or not base.strip("0"):
            base = git("rev-parse", f"{head}^").strip()
        found = []
        for commit in git("rev-list", "--reverse", "--no-merges", f"{base}..{head}").split():
            found += check([f"{commit}^", commit], f"{commit}:", git("log", "-1", "--format=%B", commit), f"{commit[:8]} ")
    for problem in found:
        print(problem)
    if found:
        print("确有必要时在提交说明里另起一段 Protected-Change: <依据>（密钥与用户标识没有例外）。")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())

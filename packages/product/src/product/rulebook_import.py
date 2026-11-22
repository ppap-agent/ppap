"""Operator command: bring a rulebook revision prepared offline into an enterprise.

The customer's rulebook arrives offline. The platform splits it into clauses as a revision directory
(``<customer>/rulebook/<version>/m<revision>/rules/<type>.json``, see assets/uaes/AGENTS.md). Then:

    python -m product.rulebook_import check  <revision dir> --original <customer's file>
    python -m product.rulebook_import bundle <revision dir> --original <customer's file> > revision.json

then a platform operator, signed in to PPAP, posts revision.json as the body of ``POST /rulebooks/<customer>/revisions``.

``check`` and ``bundle`` read no database: every clause must be named and numbered as the
rulebook rules say, and its text must appear word for word in the original, sentence by sentence (spacing and Markdown markup aside). A revision after
another carries the comparison table's notes as its grounds. The API writes it once, through the same checks as the
clarification loop; the same revision again is the one already written, other content under the same name is refused.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from product import customers, rulebooks

_SPACE = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[。；！？])|\n+")
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>|~&])")
# Emphasis and code marks, and heading lines: how the file is laid out, not the rule's words.
_MARKUP = re.compile(r"\*\*|__|`|^#{1,6} .*$", re.M)


def original_text(path: Path) -> str:
    """The customer's file as text: plain text as written, office files and PDFs through the document extractors."""
    if path.suffix.lower() == ".md":
        return _MARKUP.sub("", _ESCAPE.sub(r"\1", path.read_text(encoding="utf-8")))
    if path.suffix.lower() == ".txt":
        return path.read_text(encoding="utf-8")
    from documents.extractors.factory import get_extractor

    return get_extractor(path.suffix.lower()).extract_text(path.read_bytes())


def not_in_original(documents: list[dict], original: str) -> list[str]:
    """Clauses with a sentence that does not appear word for word in the original; spacing does not count. Checked by
    sentence because a clause may repeat its section's shared scope before its own text, as the rulebook lays it out."""
    haystack = _SPACE.sub("", original)

    def found(text: str) -> bool:
        return all(sentence in haystack for sentence in (_SPACE.sub("", part) for part in _SENTENCE.split(text)) if sentence)

    return [clause["id"] for document in documents for clause in document["clauses"] if not found(clause["text"])]


def revision_from(directory: Path) -> dict:
    """The revision a directory holds, as the revision store takes it."""
    directory = directory.resolve()
    match = re.fullmatch(r"m(\d+)", directory.name)
    version, customer = directory.parent.name, directory.parent.parent.parent.name
    if not match or directory.parent.parent.name != "rulebook" or customers.find(customer) is None:
        raise SystemExit("[导入] 目录要是 <客户>/rulebook/<版本>/m<修订>")
    revision = int(match.group(1))
    found = rulebooks.read_revision(directory.parent.parent.parent.parent, customer, version, revision)
    if found is None:
        raise SystemExit("[导入] 这个目录里没有 rules/*.json")
    documents = [{"key": d["key"], "title": d["title"], "clauses": [{k: c[k] for k in ("id", "category", "text")} for c in d["clauses"]]} for d in found["documents"]]
    changes = rulebooks.recorded_changes(found["documents"])
    base = found["base"] if revision > 0 or found["base"] else None
    return {"customer_id": customer, "version": version, "revision": revision, "base": base, "documents": documents, "changes": changes, "origin": "local"}


def checked(directory: Path, original: Path) -> dict:
    bundle = revision_from(directory)
    problems = rulebooks.revision_problems(bundle["documents"], bundle["changes"], None)
    missing = not_in_original(bundle["documents"], original_text(original))
    if missing:
        problems.append(f"{len(missing)} 条条款在原件里找不到原文：" + "、".join(missing[:30]) + ("……" if len(missing) > 30 else ""))
    if problems:
        raise SystemExit("[导入] " + "；".join(problems))
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["check", "bundle"])
    parser.add_argument("directory", nargs="?", type=Path)
    parser.add_argument("--original", type=Path, help="the customer's rulebook as received (md, txt, docx, pdf, xlsx)")
    args = parser.parse_args(argv)
    if args.directory is None or args.original is None:
        parser.error("the revision directory and --original are needed")
    bundle = checked(args.directory, args.original)
    if args.action == "check":
        count = sum(len(document["clauses"]) for document in bundle["documents"])
        print(f"可以导入：{bundle['customer_id']} {bundle['version']} 修订 {bundle['revision']}，{len(bundle['documents'])} 类文件、{count} 条条款，原文都在原件里。")
    else:
        customer = bundle.pop("customer_id")
        json.dump(bundle, sys.stdout, ensure_ascii=False)
        print(f"POST /rulebooks/{customer}/revisions", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

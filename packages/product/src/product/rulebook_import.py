"""Operator command: bring a rulebook revision prepared offline into an enterprise.

The customer's rulebook arrives offline. The platform splits it into clauses as a revision directory
(``<customer>/rulebook/<version>/m<revision>/rules/<type>.json``, see assets/uaes/AGENTS.md). Then:

    python -m product.rulebook_import check  <revision dir> --original <customer's file>
    python -m product.rulebook_import bundle <revision dir> --original <customer's file> > revision.json
    python -m product.rulebook_import load --operator <tenant_key:open_id> < revision.json

``check`` and ``bundle`` run where the files are and read no database: every clause must be named and numbered as the
rulebook rules say, and its text must appear word for word in the original, sentence by sentence (spacing and Markdown markup aside). A revision after
another carries the comparison table's notes as its grounds. ``load`` runs where the database is (the API pod) as a
platform operator of the enterprise and writes the revision once, through the same checks as the clarification loop;
the same revision again is the one already written, other content under the same name is refused.
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


def _member(store, app_id: str, tenant_key: str, subject: str):
    """The operator as a member of their enterprise, as they signed in before; nobody is created here."""
    from product.identity import Principal

    with store._conn() as conn:
        row = conn.execute(
            """SELECT i.user_id, t.id, m.role, i.display_name FROM product.identities i
               JOIN product.tenants t ON t.provider = i.provider AND t.external_key = i.tenant_key
               JOIN product.memberships m ON m.user_id = i.user_id AND m.tenant_id = t.id
               WHERE i.provider = 'feishu' AND i.app_id = %s AND i.tenant_key = %s AND i.subject = %s""",
            (app_id, tenant_key, subject),
        ).fetchone()
    return None if row is None else Principal(str(row[0]), str(row[1]), "feishu", app_id, tenant_key, subject, row[2], row[3] or "")


def load(bundle: dict, operator: str, store=None) -> dict:
    from product import build_product_store
    from product.settings import load_settings
    from product.store import rulebook_revisions
    from product.template_library import template_root

    settings = store.settings if store is not None else load_settings()
    tenant_key, _, subject = operator.partition(":")
    if not settings.platform_operator(tenant_key, subject):
        raise SystemExit("[导入] 这个身份不在平台运营名单里")
    store = store or build_product_store(settings, role="api")
    principal = _member(store, settings.feishu_app_id, tenant_key, subject)
    if principal is None:
        raise SystemExit("[导入] 这个企业里没有这个身份；请先用这个账号登录一次 PPAP")
    body = {key: bundle[key] for key in ("version", "revision", "base", "documents", "changes", "origin")}
    try:
        return rulebook_revisions.create_revision(store, principal, bundle["customer_id"], body, template_root(settings))
    except ValueError as exc:
        raise SystemExit(f"[导入] 未写入：{exc}") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["check", "bundle", "load"])
    parser.add_argument("directory", nargs="?", type=Path)
    parser.add_argument("--original", type=Path, help="the customer's rulebook as received (md, txt, docx, pdf, xlsx)")
    parser.add_argument("--operator", help="tenant_key:open_id of a platform operator of the enterprise")
    args = parser.parse_args(argv)
    if args.action in {"check", "bundle"}:
        if args.directory is None or args.original is None:
            parser.error("check and bundle need the revision directory and --original")
        bundle = checked(args.directory, args.original)
        if args.action == "check":
            count = sum(len(document["clauses"]) for document in bundle["documents"])
            print(f"可以导入：{bundle['customer_id']} {bundle['version']} 修订 {bundle['revision']}，{len(bundle['documents'])} 类文件、{count} 条条款，原文都在原件里。")
        else:
            json.dump(bundle, sys.stdout, ensure_ascii=False)
        return 0
    if not args.operator:
        parser.error("load needs --operator")
    found = load(json.load(sys.stdin), args.operator)
    print(f"已写入：{found['customer_id']} {found['version']} 修订 {found['revision']}，{found['clause_count']} 条条款。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

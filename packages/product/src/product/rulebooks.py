"""Rulebook revisions shipped with the product, read only.

A customer's rulebook lives under ``<template_root>/<customer>/rulebook/<version>/m<revision>/`` (see
assets/uaes/AGENTS.md): ``rules/*.json`` holds one document type's clauses each. A version directory may carry the
comparison table that leads into one of its revisions (``<from> 到 <version>-m<revision> 对照.md``), whose rows say per
clause whether its meaning changed. A clause is named by its document type and number as the customer's rulebook has
them (``CP:5`` in ``rules/CP.json``); every revision of a version has the same clause names, so a changed name means a
new version (``problems`` checks this). Nothing here is customer specific, writes anything or says which revision audits
use.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

_VERSION = re.compile(r"^v\d+\.\d+$")
_REVISION = re.compile(r"^m(\d+)$")
_KINDS = {"意思不变": "same", "意思变了": "changed", "新增": "new"}
_CLAUSE = re.compile(r"^([A-Z][A-Z0-9]*):(\d+)$")


def _customer_root(root: Path, customer_id: str) -> Path | None:
    """Only a directory that exists under the root names a customer; nothing from the request is joined into a path."""
    if not root.is_dir():
        return None
    return next((path / "rulebook" for path in root.iterdir() if path.name == customer_id and (path / "rulebook").is_dir()), None)


def shipped_customers(root: Path) -> list[str]:
    """The customers the product ships a rulebook for: the directories under the root that hold one."""
    return sorted(path.name for path in root.iterdir() if (path / "rulebook").is_dir() and len(path.name) <= 64) if root.is_dir() else []


def _revision_dirs(rulebook: Path) -> list[tuple[str, int, Path]]:
    found = []
    for version in sorted(path for path in rulebook.iterdir() if path.is_dir() and _VERSION.fullmatch(path.name)):
        for revision in sorted(path for path in version.iterdir() if path.is_dir() and _REVISION.fullmatch(path.name) and (path / "rules").is_dir()):
            found.append((version.name, int(_REVISION.fullmatch(revision.name).group(1)), revision))  # type: ignore[union-attr]
    return sorted(found, key=lambda item: (tuple(int(part) for part in item[0][1:].split(".")), item[1]))


def _documents(directory: Path, version: str) -> list[dict[str, Any]]:
    suffix = re.compile(r"（[^（）]*" + re.escape(version) + r"[^（）]*）$")
    documents = []
    for path in sorted((directory / "rules").glob("*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        clauses = [{"id": str(row["id"]), "category": re.sub(r"^[A-Z]+-[A-Z0-9]+-", "", str(row.get("category") or "")), "text": str(row["rule_to_check"])} for row in body.get("rules") or []]
        documents.append({"key": path.stem, "title": suffix.sub("", str(body.get("title") or path.stem)).strip(), "clauses": clauses})
    return documents


def _ordered(documents: list[dict[str, Any]], changes: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """The rulebook's own order of document types, as its comparison table lists them; file names otherwise."""
    order = {clause: index for index, clause in enumerate(changes)}
    return sorted(documents, key=lambda document: min((order.get(clause["id"], len(order)) for clause in document["clauses"]), default=len(order)))


def _changes(rulebook: Path, version: str, revision: int) -> tuple[dict[str, Any] | None, dict[str, dict[str, str]]]:
    """The comparison into this revision: the revision it starts from and, per clause, how it changed."""
    target = f"{version}-m{revision}"
    tables = sorted((rulebook / version).glob(f"* 到 {target} 对照.md"))
    if len(tables) != 1:
        return None, {}
    changes = {}
    for line in tables[0].read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")] if line.startswith("|") else []
        kind = _KINDS.get(cells[2].removesuffix("（待复核）")) if len(cells) == 5 else None
        if kind:
            changes[cells[0]] = {"kind": kind, "note": cells[3]}
    base = re.fullmatch(r"(v\d+\.\d+)-m(\d+)", tables[0].name.split(" 到 ", 1)[0])
    return ({"version": base.group(1), "revision": int(base.group(2))} if base else None), changes


def _summary(version: str, revision: int, documents: list[dict[str, Any]], changes: dict[str, dict[str, str]], base: dict[str, Any] | None) -> dict[str, Any]:
    counts = Counter(changes[clause["id"]]["kind"] for document in documents for clause in document["clauses"] if clause["id"] in changes)
    return {
        "version": version,
        "revision": revision,
        "clause_count": sum(len(document["clauses"]) for document in documents),
        "base": base,
        "changes": {kind: counts.get(kind, 0) for kind in ("same", "changed", "new")} if changes else None,
    }


def list_revisions(root: Path, customer_id: str) -> list[dict[str, Any]]:
    """Every revision of a customer's rulebook, oldest first; empty when the customer has none."""
    rulebook = _customer_root(root, customer_id)
    if rulebook is None:
        return []
    rows = []
    for version, revision, directory in _revision_dirs(rulebook):
        base, changes = _changes(rulebook, version, revision)
        rows.append(_summary(version, revision, _documents(directory, version), changes, base))
    return rows


def read_revision(root: Path, customer_id: str, version: str, revision: int) -> dict[str, Any] | None:
    """One revision's clauses by document type, each with how it changed from the revision before."""
    rulebook = _customer_root(root, customer_id)
    directory = next((path for name, number, path in _revision_dirs(rulebook) if (name, number) == (version, revision)), None) if rulebook else None
    if rulebook is None or directory is None:
        return None
    base, changes = _changes(rulebook, version, revision)
    documents = _ordered(_documents(directory, version), changes)
    for document in documents:
        for clause in document["clauses"]:
            clause["change"] = changes.get(clause["id"])
    return {**_summary(version, revision, documents, changes, base), "documents": documents}


def problems(root: Path, customer_id: str) -> list[str]:
    """Where a customer's rulebook breaks its naming: each clause is «<file's document type>:<number>», unique in its
    revision, and every revision of a version names the same clauses (a name added, dropped or renamed is a new version)."""
    rulebook = _customer_root(root, customer_id)
    if rulebook is None:
        return []
    found: list[str] = []
    names: dict[str, tuple[int, set[str]]] = {}
    for version, revision, directory in _revision_dirs(rulebook):
        ids = []
        for path in sorted((directory / "rules").glob("*.json")):
            for row in json.loads(path.read_text(encoding="utf-8")).get("rules") or []:
                clause = str(row.get("id"))
                match = _CLAUSE.fullmatch(clause)
                if not match or match.group(1) != path.stem:
                    found.append(f"{version} 修订 {revision} {path.name}：{clause} 不是「{path.stem}:编号」")
                ids.append(clause)
        found += [f"{version} 修订 {revision}：{clause} 出现 {count} 次" for clause, count in Counter(ids).items() if count > 1]
        first = names.setdefault(version, (revision, set(ids)))
        if set(ids) != first[1]:
            added, dropped = sorted(set(ids) - first[1]), sorted(first[1] - set(ids))
            found.append(f"{version} 修订 {revision} 的条款编号与修订 {first[0]} 不同（多 {'、'.join(added) or '无'}，少 {'、'.join(dropped) or '无'}）；编号变了应升版本")
    return found


# ---- a revision as data: what the clarification loop and the offline import both write -------------------------------

_KEY = re.compile(r"^[A-Z][A-Z0-9]*$")
_QUOTED = re.compile(r"[“「『]([^”」』]{1,80})[”」』]")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
MAX_CLAUSE = 20000
READING_PARTS = ("checks", "passes", "fails", "manual")


def digest(documents: list[dict[str, Any]]) -> str:
    """One revision's content: the same clauses in the same order give the same digest."""
    canonical = [{"key": d["key"], "title": d["title"], "clauses": [{"id": c["id"], "category": c["category"], "text": c["text"]} for c in d["clauses"]]} for d in documents]
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def clause_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def clauses_of(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {clause["id"]: clause for document in documents for clause in document["clauses"]}


def _text(value: Any, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= limit


def _sourced(change: Any) -> bool:
    """A change names what it rests on: answered question cards, or a ruling in the customer's or the original's words."""
    if not isinstance(change, dict) or not _text(change.get("note"), 2000):
        return False
    sources = change.get("sources")
    if not isinstance(sources, list) or not sources:
        return False
    for source in sources:
        if not isinstance(source, dict):
            return False
        if "question_key" in source:
            if not _text(source["question_key"], 200) or not isinstance(source.get("answerers"), list) or not source["answerers"] or not all(_text(name, 200) for name in source["answerers"]):
                return False
        elif not _text(source.get("ruling"), 2000):
            return False
    return True


def revision_problems(documents: Any, changes: Any, base_documents: list[dict[str, Any]] | None) -> list[str]:
    """Why these clauses cannot be a revision. `base_documents` is the revision before it in the same version: the next
    revision keeps its clause names, and every clause whose text differs says how its meaning changed and on what grounds."""
    if not isinstance(documents, list) or not documents or not isinstance(changes, dict):
        return ["修订内容格式不对"]
    found: list[str] = []
    ids: list[str] = []
    for document in documents:
        if not isinstance(document, dict) or not isinstance(document.get("key"), str) or not _KEY.fullmatch(document["key"]) or not _text(document.get("title"), 200):
            return ["文件类型的缩写或名称不对"]
        clauses = document.get("clauses")
        if not isinstance(clauses, list) or not clauses:
            found.append(f"{document['key']} 没有条款")
            continue
        for clause in clauses:
            if not isinstance(clause, dict) or not isinstance(clause.get("id"), str) or not isinstance(clause.get("category"), str) or not _text(clause.get("text"), MAX_CLAUSE):
                return [f"{document['key']} 有条款格式不对"]
            match = _CLAUSE.fullmatch(clause["id"])
            if not match or match.group(1) != document["key"]:
                found.append(f"{clause['id']} 不是「{document['key']}:编号」")
            ids.append(clause["id"])
    found += [f"{clause} 出现 {count} 次" for clause, count in Counter(ids).items() if count > 1]
    for clause, change in changes.items():
        if clause not in ids or not isinstance(change, dict) or change.get("kind") not in _KINDS.values() or not isinstance(change.get("note", ""), str):
            found.append(f"{clause} 的改动说明不对")
    if found or base_documents is None:
        return found
    before, after = clauses_of(base_documents), clauses_of(documents)
    if set(before) != set(after):
        added, dropped = sorted(set(after) - set(before)), sorted(set(before) - set(after))
        return [f"条款编号与上一修订不同（多 {'、'.join(added) or '无'}，少 {'、'.join(dropped) or '无'}）；编号变了应升版本"]
    for clause, row in after.items():
        change = changes.get(clause)
        if row["text"] == before[clause]["text"]:
            if change is not None and change.get("kind") != "same":
                found.append(f"{clause} 原文没有变，不能标为意思变了或新增")
        elif change is None or change.get("kind") not in {"same", "changed"} or not _sourced(change):
            found.append(f"{clause} 的原文改了，但没有写明意思变没变和依据")
    return found


def reading_problems(clause_text: str, reading: Any) -> list[str]:
    """Why this reading cannot stand beside the clause: it must be complete and keep every quoted term and number of
    the original. It cannot prove the reading means the same; a person confirms that."""
    if not isinstance(reading, dict) or set(reading) != set(READING_PARTS):
        return ["解读要分为：查什么、何时通过、何时不通过、何时转人工"]
    for part in READING_PARTS:
        items = reading[part]
        if not isinstance(items, list) or len(items) > 30 or not all(_text(item, 1000) for item in items):
            return ["解读的条目格式不对"]
    if not reading["checks"] or not (reading["passes"] or reading["fails"]):
        return ["解读至少要写明查什么，以及何时通过或不通过"]
    joined = "".join(item for part in READING_PARTS for item in reading[part])
    missing = [term for term in _QUOTED.findall(clause_text) if term.strip() and term.strip() not in joined]
    missing += [number for number in _NUMBER.findall(clause_text) if number not in joined]
    return ["解读缺少原文内容：" + "、".join(dict.fromkeys(missing))[:300]] if missing else []

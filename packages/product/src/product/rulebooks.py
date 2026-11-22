"""Rulebook revisions shipped with the product, read only.

A customer's rulebook lives under ``<template_root>/<customer>/rulebook/<version>/m<revision>/`` (see
assets/uaes/AGENTS.md): ``rules/*.json`` holds one document type's clauses each. A version directory may carry the
comparison table that leads into one of its revisions (``<from> 到 <version>-m<revision> 对照.md``), whose rows say per
clause whether its meaning changed. A clause is named by its document type and number as the customer's rulebook has
them (``CP:5`` in ``rules/CP.json``); every revision of a version has the same clause names, so a changed name means a
new version (``problems`` checks this). Nothing here is customer specific, writes anything or decides which revision audits
use: a revision is "in audit" only when its clauses are exactly the product presets the audits run.
"""

from __future__ import annotations

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
        documents.append({"title": suffix.sub("", str(body.get("title") or path.stem)).strip(), "clauses": clauses})
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


def _in_audit(customer_id: str, documents: list[dict[str, Any]]) -> bool:
    from audit.presets import customer_presets
    from audit.standard_suites import customer_directory

    audited = Counter(clause.text for preset in customer_presets() if customer_directory(preset.customer) == customer_id for clause in preset.clauses)
    return bool(audited) and audited == Counter(clause["text"] for document in documents for clause in document["clauses"])


def _summary(customer_id: str, version: str, revision: int, documents: list[dict[str, Any]], changes: dict[str, dict[str, str]], base: dict[str, Any] | None) -> dict[str, Any]:
    counts = Counter(changes[clause["id"]]["kind"] for document in documents for clause in document["clauses"] if clause["id"] in changes)
    return {
        "version": version,
        "revision": revision,
        "in_audit": _in_audit(customer_id, documents),
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
        rows.append(_summary(customer_id, version, revision, _documents(directory, version), changes, base))
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
    return {**_summary(customer_id, version, revision, documents, changes, base), "documents": documents}


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

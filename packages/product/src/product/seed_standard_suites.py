"""Explicit platform installation of standard sources, never fake validation.

Run after schema migrations with platform migrator/object credentials. No
customer originals are copied and no publication/verification proof is created.
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from audit.standard_suites import predefined_directory_suites, standard_suites
from audit.suite_migration import generic_standard_suites

# A predefined leaf path. Asset docx paths and the shared oracle.csv path are not
# this identity: several historical suites share that csv path, and their rows
# stay addressable by content digest.
_DIRECTORY_SOURCE = re.compile(r"^v[0-9]+/[^/]+/[^/]+$")


def official_source_suites():
    """Legacy carriers, directory leaves, then historical generics.

    Directory leaves keep the business suite_key. Identical file bytes keep one
    content digest; ``v5/UAES/U08`` and ``v6/UAES/U08`` are still two revisions.
    """
    return (*standard_suites(), *predefined_directory_suites(), *generic_standard_suites(include_history=True))


def persisted_source_path(source_path: str) -> str | None:
    """Only a directory leaf is stored on the official revision row."""
    return source_path if _DIRECTORY_SOURCE.fullmatch(source_path) else None


def match_official_revision(rows: list[tuple[str, str | None]], *, persisted_path: str | None, shared_content: bool) -> str | None:
    """Pick an existing official row without collapsing two directory paths.

    ``rows`` are ``(revision_id, source_path)`` for one suite_key and content
    digest. A directory path never reuses a historical NULL row. A unique
    legacy digest still reuses that NULL row, so old installs are not duplicated.
    """
    if persisted_path is not None:
        for revision_id, stored in rows:
            if stored == persisted_path:
                return str(revision_id)
        return None
    if shared_content:
        return None
    for revision_id, stored in rows:
        if stored is None:
            return str(revision_id)
    return None


def install_official_sources(store: Any, suites) -> list[dict[str, str]]:
    from product.suites import content_digest, resolved_digest

    prepared = []
    grouped: dict[tuple[str, str], set[str]] = {}
    for suite in suites:
        digest = resolved_digest(suite.suite_key, suite.related_suite_keys, {name: content_digest(body) for name, body in suite.files.items()})
        prepared.append((suite, digest))
        grouped.setdefault((suite.suite_key, digest), set()).add(suite.source_path)
    results = []
    with store._conn() as conn, conn.transaction():
        # All platform seeding writers use the same transaction-level lock.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('ppap-standard-suite-seed', 0))")
        for suite, digest in prepared:
            rows = [
                (str(row[0]), row[1])
                for row in conn.execute(
                    """SELECT id, source_path FROM product.suite_revisions
                       WHERE tenant_id IS NULL AND suite_key=%s AND resolved_digest=%s AND NOT history_only
                       ORDER BY revision_number, id""",
                    (suite.suite_key, digest),
                ).fetchall()
            ]
            persisted = persisted_source_path(suite.source_path)
            revision_id = match_official_revision(rows, persisted_path=persisted, shared_content=len(grouped[(suite.suite_key, digest)]) > 1)
            if revision_id is None:
                revision = store.create_revision(
                    tenant_id=None,
                    suite_key=suite.suite_key,
                    files=dict(suite.files),
                    created_by=None,
                    related_suite_keys=suite.related_suite_keys,
                    source_path=persisted,
                    conn=conn,
                )
                revision_id = revision.id
            else:
                store.resolve_suite(revision_id, "00000000-0000-0000-0000-000000000000", conn=conn)
            # source_path is the directory or asset identity. resolved_digest is content only.
            results.append({"suite_key": suite.suite_key, "source_path": suite.source_path, "revision_id": revision_id, "resolved_digest": digest, "validation": "not-created"})
    return results


def seed_standard_suites(store: Any) -> list[dict[str, str]]:
    if store.role != "migrator":
        raise PermissionError("official_suite_write_forbidden")
    return install_official_sources(store, official_source_suites())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write official revisions using configured migrator and object storage credentials.")
    args = parser.parse_args()
    if not args.apply:
        print(
            json.dumps(
                {
                    "suites": [{"suite_key": suite.suite_key, "source_path": suite.source_path} for suite in official_source_suites()],
                    "writes": False,
                    "validation": "not-created",
                },
                ensure_ascii=False,
            )
        )
        return
    from product.settings import Settings
    from product.store.suites import SuiteStore

    print(json.dumps(seed_standard_suites(SuiteStore(Settings(), role="migrator")), ensure_ascii=False))


if __name__ == "__main__":
    main()

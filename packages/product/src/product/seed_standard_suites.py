"""Explicit platform installation of standard sources, never fake validation.

Run after schema migrations with platform migrator/object credentials. No
customer originals are copied and no publication/verification proof is created.
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from audit.standard_suites import predefined_directory_suites

# A predefined leaf path. Asset docx paths and the shared oracle.csv path are not
# this identity: several historical suites share that csv path, and their rows
# stay addressable by content digest.
_DIRECTORY_SOURCE = re.compile(r"^(?!v[0-9]+/)[^/]+/[^/]+$")


def discovery_row(suite) -> dict[str, Any]:
    """One discovered source. Directory leaves also carry their stored reader limit."""
    row = {"suite_key": suite.suite_key, "source_path": suite.source_path}
    if persisted_source_path(suite.source_path):
        from product.suite_releases import leaf_capability

        row["capability"] = leaf_capability(suite.files)
    return row


def official_source_suites():
    """Directory leaves only. A business suite_key is not a version selector.

    Identical file bytes keep one content digest; the leaf path (``uaes/U08``)
    is stored beside it. Historical carriers are not installed or published by
    this path.
    """
    return predefined_directory_suites()


def persisted_source_path(source_path: str) -> str | None:
    """Only a directory leaf is stored on the official revision row."""
    return source_path if _DIRECTORY_SOURCE.fullmatch(source_path) else None


def match_official_revision(rows: list[tuple[str, str | None]], *, persisted_path: str) -> str | None:
    """Pick the official row for this directory path. A NULL row is not a match."""
    for revision_id, stored in rows:
        if stored == persisted_path:
            return str(revision_id)
    return None


def install_official_sources(store: Any, suites) -> list[dict[str, str]]:
    """Install directory leaves only. This does not validate or move a head."""
    from product.suites import content_digest, resolved_digest

    suites = tuple(suites)
    prepared = []
    for suite in suites:
        if persisted_source_path(suite.source_path) is None:
            raise ValueError("historical_source_not_seedable")
        digest = resolved_digest(suite.suite_key, suite.related_suite_keys, {name: content_digest(body) for name, body in suite.files.items()})
        prepared.append((suite, digest))
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
            if persisted is None:
                raise ValueError("historical_source_not_seedable")
            revision_id = match_official_revision(rows, persisted_path=persisted)
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


def publish_repository_defaults(releases: Any, seeded: list[dict[str, str]]) -> list[dict]:
    """Make each seeded leaf the official default where no official head exists yet.

    Repository checkers are validated offline, so they become defaults without a
    corpus proof row. A head that already exists is never replaced here. Leaves a
    related program depends on are published first.
    """
    if releases.role != "migrator":
        raise PermissionError("official_suite_write_forbidden")
    by_key = {row["suite_key"]: row for row in seeded}
    related = {suite.suite_key: suite.related_suite_keys for suite in official_source_suites()}
    published: list[dict] = []
    done: set[str] = set()

    def publish(key: str) -> None:
        if key in done or key not in by_key:
            return
        done.add(key)
        for dependency in related.get(key, ()):
            publish(dependency)
        if releases.official_active(key)["revision"] == 0:
            published.append(releases.publish_official(by_key[key]["revision_id"], expected_revision=0, expected_validation_revision=None, repository_default=True))

    for key in sorted(by_key):
        publish(key)
    return published


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write official revisions using configured migrator and object storage credentials.")
    parser.add_argument("--publish-defaults", action="store_true", help="With --apply: make each leaf the official default where no official head exists.")
    args = parser.parse_args()
    if args.publish_defaults and not args.apply:
        parser.error("--publish-defaults requires --apply")
    if not args.apply:
        print(
            json.dumps(
                {
                    "suites": [discovery_row(suite) for suite in official_source_suites()],
                    "writes": False,
                    "validation": "not-created",
                },
                ensure_ascii=False,
            )
        )
        return
    from product.settings import Settings
    from product.store.suites import SuiteStore

    store = SuiteStore(Settings(), role="migrator")
    seeded = seed_standard_suites(store)
    if not args.publish_defaults:
        print(json.dumps(seeded, ensure_ascii=False))
        return
    from product.store.suite_releases import SuiteReleaseStore

    releases = SuiteReleaseStore(store.settings, store.objects, role="migrator")
    print(json.dumps({"seeded": seeded, "published": publish_repository_defaults(releases, seeded)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

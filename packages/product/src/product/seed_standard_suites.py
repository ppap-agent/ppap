"""Explicit platform installation of standard sources, never fake validation.

Run after schema migrations with platform migrator/object credentials. No
customer originals are copied and no publication/verification proof is created.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from audit.standard_suites import standard_suites
from audit.suite_migration import generic_standard_suites


def seed_standard_suites(store: Any) -> list[dict[str, str]]:
    from product.suites import content_digest, resolved_digest

    if store.role != "migrator":
        raise PermissionError("official_suite_write_forbidden")
    results = []
    with store._conn() as conn, conn.transaction():
        # All platform seeding writers use the same transaction-level lock.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('ppap-standard-suite-seed', 0))")
        for suite in (*standard_suites(), *generic_standard_suites(include_history=True)):
            digest = resolved_digest(suite.suite_key, suite.related_suite_keys, {name: content_digest(body) for name, body in suite.files.items()})
            current = conn.execute(
                "SELECT id FROM product.suite_revisions WHERE tenant_id IS NULL AND suite_key=%s AND resolved_digest=%s ORDER BY created_at DESC, id DESC LIMIT 1",
                (suite.suite_key, digest),
            ).fetchone()
            if current:
                revision_id = str(current[0])
                store.resolve_suite(revision_id, "00000000-0000-0000-0000-000000000000", conn=conn)
            else:
                revision = store.create_revision(tenant_id=None, suite_key=suite.suite_key, files=dict(suite.files), created_by=None, related_suite_keys=suite.related_suite_keys, conn=conn)
                revision_id = revision.id
            results.append({"suite_key": suite.suite_key, "revision_id": revision_id, "resolved_digest": digest, "validation": "not-created"})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write official revisions using configured migrator and object storage credentials.")
    args = parser.parse_args()
    if not args.apply:
        print(
            json.dumps(
                {"suites": [suite.suite_key for suite in (*standard_suites(), *generic_standard_suites(include_history=True))], "writes": False, "validation": "not-created"}, ensure_ascii=False
            )
        )
        return
    from product.settings import Settings
    from product.store.suites import SuiteStore

    print(json.dumps(seed_standard_suites(SuiteStore(Settings(), role="migrator")), ensure_ascii=False))


if __name__ == "__main__":
    main()

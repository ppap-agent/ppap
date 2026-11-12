"""Import legacy Round identities without executing an audit or a Checker.

Default is a database READ ONLY dry-run. --apply additionally requires the
exact --expect-digest printed by a reviewed dry-run. Production use is a
separately authorized #218 operation, never an application startup step.
"""

from __future__ import annotations

import argparse
import json

from product import build_product_store
from product.checker_history import HistoryImportError, import_history, read_history
from product.identity import Principal
from product.settings import Settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="Exact tenant UUID; no all-tenant import.")
    parser.add_argument("--round-id", action="append", help="Optional exact Round UUIDs; otherwise all legacy Rounds in this tenant.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-digest", help="Required with --apply; reviewed dry-run plan digest.")
    parser.add_argument("--verify-user-id", help="Read back selected Rounds with this existing user's current membership and object grants; no writes.")
    args = parser.parse_args(argv)
    if args.apply and (not args.expect_digest or args.verify_user_id):
        parser.error("--apply requires --expect-digest and cannot combine with --verify-user-id")
    if args.verify_user_id and not args.round_id:
        parser.error("--verify-user-id requires one or more --round-id")
    try:
        store = build_product_store(Settings(), role="api" if args.verify_user_id else "migrator")
        if args.verify_user_id:
            principal = Principal(args.verify_user_id, args.tenant_id, "", "", "", "", "member")
            result = {"writes": False, "rounds": [{"round_id": ident, "identities": read_history(store, principal, ident)} for ident in args.round_id]}
        else:
            result = import_history(store, tenant_id=args.tenant_id, apply=args.apply, expected_digest=args.expect_digest, round_ids=args.round_id)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0 if result.get("complete", True) else 2
    except HistoryImportError as exc:
        print(json.dumps({"complete": False, "error": str(exc)}))
        return 2
    except Exception:
        print(json.dumps({"complete": False, "error": "history_operation_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

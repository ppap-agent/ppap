"""Which clause wordings the installed checker programs are written against (docs/rules.md, step 7).

A checker leaf's rules.json carries every clause it checks in the very wording it implements. A clause of a rulebook
revision has a checker when a bound rule of its customer's leaves has the same clause id and the same wording digest:
change the wording and the match is gone, until the program is written against the new one. The leaves are the record;
there is no separate alignment ledger. Candidate programs kept outside the installed tree do not count.
"""

from __future__ import annotations

import json
from functools import lru_cache

from audit.standard_suites import CUSTOMER_DIRECTORIES, predefined_directory_suites

from product import rulebooks


@lru_cache(maxsize=1)
def written() -> frozenset[tuple[str, str, str]]:
    """(customer id, clause id, wording digest) for every clause an installed leaf binds a program to."""
    customer_of = {name: directory for directory, name in CUSTOMER_DIRECTORIES.items()}
    found: set[tuple[str, str, str]] = set()
    for suite in predefined_directory_suites():
        rules = json.loads(suite.files["rules.json"])
        for row in rules["rules"]:
            if row["id"] in suite.entrypoints and isinstance(row.get("rule_to_check"), str):
                found.add((customer_of[suite.customer], row["id"], rulebooks.clause_digest(row["rule_to_check"])))
    return frozenset(found)


def has_checker(customer_id: str, clause_id: str, digest: str) -> bool:
    return (customer_id, clause_id, digest) in written()

"""A clause has a checker only in the very wording an installed leaf binds a program to."""

import json

from audit.standard_suites import predefined_directory_suites
from product import checker_wordings, rulebooks


def test_a_clause_has_a_checker_only_in_the_wording_the_leaf_carries():
    suite = next(suite for suite in predefined_directory_suites() if suite.customer == "UAES" and suite.entrypoints)
    rule = next(row for row in json.loads(suite.files["rules.json"])["rules"] if row["id"] in suite.entrypoints)
    digest = rulebooks.clause_digest(rule["rule_to_check"])
    assert checker_wordings.has_checker("uaes", rule["id"], digest)
    assert not checker_wordings.has_checker("uaes", rule["id"], rulebooks.clause_digest(rule["rule_to_check"] + "另补一句。"))
    assert not checker_wordings.has_checker("li", rule["id"], digest)

import json

import pytest
from product.rules import customer_scope


@pytest.mark.parametrize(
    "customers,registration,expected",
    [
        ([], None, "unconfirmed"),
        ([], "not-json", "unconfirmed"),
        ([], {"scope": {"allCustomers": False}}, "unconfirmed"),
        ([], {"scope": {"allCustomers": "true"}}, "unconfirmed"),
        ([], {"scope": {"allCustomers": True}}, "all"),
        (["NIO"], None, "restricted"),
        (["NIO"], {"scope": {"allCustomers": True}}, "restricted"),
    ],
)
def test_customer_scope_requires_an_explicit_unrestricted_declaration(customers, registration, expected):
    context = {"registration": json.dumps(registration) if isinstance(registration, dict) else registration}
    assert customer_scope({"customers": customers}, context) == expected


def test_scope_only_version_can_confirm_customers_without_reconfirming_rule_content():
    assert customer_scope({"customers": []}, {"customer_scope": "all"}) == "all"
    assert customer_scope({"customers": []}, {"customer_scope": True}) == "unconfirmed"
    assert customer_scope({"customers": ["NIO"]}, {"customer_scope": "all"}) == "restricted"

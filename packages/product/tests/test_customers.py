"""The customer register: exact names only, a versioned type set M, and two independent coverage numerators."""

from dataclasses import replace

from audit.presets import customer_presets, preset_rule_body
from product import customers


def preset_rules():
    """The product presets as a tenant lists them after installation."""
    return [{**preset_rule_body(preset.customer, preset.entry_id), "id": preset.entry_id} for preset in customer_presets()]


def test_only_registered_names_resolve_and_unregistered_values_are_returned_as_recorded():
    assert [customers.resolve(name).id for name in ("UAES", "联合汽车电子有限公司", " 理想 ", "理想汽车", "蔚来", "蔚来汽车")] == ["uaes", "uaes", "li", "li", "nio", "nio"]
    for name in ("UAES（联电）", "联合汽车电子", "联合汽车电子（UAES）", "uaes", "理想 汽车", "", None, "li"):
        assert customers.resolve(name) is None
    assert customers.full_name("UAES") == "联合汽车电子有限公司"
    assert customers.short_name("蔚来汽车") == "蔚来"
    assert customers.full_name("长城汽车") == "长城汽车"


def test_type_sets_and_rule_coverage_from_the_installed_presets():
    rules = preset_rules()
    sizes = {}
    for customer in customers.CUSTOMERS:
        value = customers.coverage(customer, (), customers.rule_type_codes(customer, rules))
        sizes[customer.id] = (len(value["rule_types"]), len(value["types"]))
    # UAES: 8 T types and E01–E11; E12–E14 are counterpart documents only. 蔚来 presets cover 17 types but M is 5.
    assert sizes == {"uaes": (19, 19), "li": (17, 17), "nio": (5, 5)}
    uaes = customers.find("uaes").type_set.types
    assert [code for code in uaes if code.startswith("E")] == [f"E{index:02d}" for index in range(1, 12)]
    assert {"E12", "E13", "E14"}.isdisjoint(uaes)


def test_the_inspection_report_is_t01_for_li_and_t01a_for_nio():
    # PRD §1.5: 蔚来's measurement report (01-2) is the pool member T01a; 理想's inspection report (01-1) stays T01.
    assert customers.find("nio").type_set.types == ("T01a", "T09", "T10", "T11", "T12")
    assert (customers.type_code("全功能检验报告", "蔚来"), customers.type_code("全功能检验报告", "理想"), customers.type_code("全功能检验报告")) == ("T01a", "T01", "T01")
    assert customers.type_name("T01a") == customers.type_name("T01") == "全功能检验报告"
    rules = preset_rules()
    assert "T01a" in customers.rule_type_codes(customers.find("nio"), rules)
    assert "T01" in customers.rule_type_codes(customers.find("li"), rules) and "T01a" not in customers.rule_type_codes(customers.find("li"), rules)
    from product.customers import in_type_set

    assert in_type_set({"customers": ["蔚来"], "type": "全功能检验报告"}) and in_type_set({"customers": ["理想"], "type": "全功能检验报告"})
    assert not in_type_set({"customers": ["UAES"], "type": "全功能检验报告"})


def test_rule_numerator_counts_each_type_once_and_only_rules_change_it():
    li = customers.find("li")
    rules = [rule for rule in preset_rules() if rule["customers"] == ["理想"]]
    second = {**rules[0], "id": "second", "title": "同类型第二条"}
    assert customers.coverage(li, (), customers.rule_type_codes(li, [*rules, second]))["rule_types"] == customers.coverage(li, (), customers.rule_type_codes(li, rules))["rule_types"]
    without = [rule for rule in rules if rule["type"] != "控制计划"]
    value = customers.coverage(li, ["T01", "T11"], customers.rule_type_codes(li, without))
    assert (len(value["template_types"]), len(value["rule_types"]), len(value["types"])) == (2, 16, 17)
    # A rule for another customer or an unregistered look-alike name does not count.
    other = [{**rule, "customers": ["理想 汽车"]} for rule in rules]
    assert customers.rule_type_codes(li, other) == set()


def test_missing_type_set_yields_no_coverage():
    nio = replace(customers.find("nio"), type_set=None)
    assert customers.coverage(nio, ["T01"], ["T01"]) is None
    assert customers.coverage(replace(nio, type_set=customers.TypeSet("x", "2026-11-18", ())), ["T01"], ["T01"]) is None


def test_a_registered_customer_rule_outside_its_type_set_is_not_offered_to_new_tasks():
    from product.customers import in_type_set

    # 蔚来 M is its five types: its N06–N17 presets (for example 零部件设计验证计划和报告, T02) stay stored but are not offered.
    assert not in_type_set({"customers": ["蔚来"], "type": "零部件设计验证计划和报告"})
    assert in_type_set({"customers": ["蔚来"], "type": "控制计划"})
    assert in_type_set({"customers": ["理想"], "type": "零部件设计验证计划和报告"})
    # Not judged here: an unregistered customer, a rule without customers, an unrecognised type.
    assert in_type_set({"customers": ["示例客户"], "type": "零部件设计验证计划和报告"})
    assert in_type_set({"customers": [], "type": "零部件设计验证计划和报告"})
    assert in_type_set({"customers": ["蔚来"], "type": "8D报告"})

import unittest

from ppap_core.rule_coverage import (
    COVERAGE_MAPPED,
    COVERAGE_UNSUPPORTED,
    MODE_LEGACY_BUILTIN,
    MODE_RULE_ITEMS,
    REASON_MANUAL_ONLY,
    freeze_rule_execution,
    map_rule_items,
)

DECLARATIONS = (
    ("rule_1", "文件类型勾选审核"),
    ("rule_2", "过程流程图与控制计划列项及一致性审核"),
)


class RuleCoverageTests(unittest.TestCase):
    def test_confirmed_rules_follow_fixed_checker_order_without_content_adjudication(self):
        items = [
            {"id": "customer-b", "category": "客户自定标题", "rule_to_check": "用户已确认的规则正文，研发不解释其语义。"},
            {"id": "customer-a", "category": "另一标题", "rule_to_check": "前半为过程字段，后半为产品字段，仍按当前固定顺序执行。"},
        ]
        mapped = map_rule_items(items, DECLARATIONS)
        self.assertEqual([item["coverage"] for item in mapped], [COVERAGE_MAPPED, COVERAGE_MAPPED])
        self.assertEqual([item["checker_index"] for item in mapped], [0, 1])
        self.assertEqual([item["checker_method"] for item in mapped], ["rule_1", "rule_2"])
        self.assertEqual([item["rule_to_check"] for item in mapped], [item["rule_to_check"] for item in items])

    def test_confirmed_text_is_preserved_without_needing_a_matching_builtin_description(self):
        items = [
            {"id": "keep", "category": "文件", "priority": "P1", "rule_to_check": "文件类型勾选审核"},
            {"id": "extra", "category": "customer-only", "priority": None, "rule_to_check": "arbitrary customer requirement"},
        ]
        mapped = map_rule_items(items, DECLARATIONS)
        self.assertEqual(mapped[0]["coverage"], COVERAGE_MAPPED)
        self.assertEqual(mapped[0]["checker_method"], "rule_1")
        self.assertEqual(mapped[0]["checker_index"], 0)
        self.assertEqual(mapped[1]["coverage"], COVERAGE_MAPPED)
        self.assertEqual(mapped[1]["checker_index"], 1)
        self.assertEqual(mapped[1]["rule_to_check"], items[1]["rule_to_check"])

    def test_fixed_order_does_not_require_a_semantic_match(self):
        items = [{"id": "1", "category": "基本信息", "priority": "P0", "rule_to_check": "必须填写零件号"}]
        mapped = map_rule_items(items, DECLARATIONS)
        self.assertEqual((mapped[0]["coverage"], mapped[0]["checker_index"]), (COVERAGE_MAPPED, 0))

    def test_categories_and_repeated_text_do_not_reorder_the_confirmed_list(self):
        items = [
            {"id": "first", "category": "文件类型勾选审核", "rule_to_check": "必须勾选文件类型"},
            {"id": "different", "category": "文件类型勾选审核", "rule_to_check": "必须勾选文件类型"},
        ]
        mapped = map_rule_items(items, DECLARATIONS)
        self.assertEqual([item["coverage"] for item in mapped], [COVERAGE_MAPPED] * 2)
        self.assertEqual([item["checker_index"] for item in mapped], [0, 1])

    def test_empty_rules_keep_legacy_builtin_mode(self):
        frozen = freeze_rule_execution({"kind": "engine", "type": "控制计划", "rules": []}, DECLARATIONS)
        self.assertEqual(frozen["mode"], MODE_LEGACY_BUILTIN)
        presence = freeze_rule_execution({"kind": "presence"}, DECLARATIONS)
        self.assertIsNone(presence)

    def test_freeze_keeps_item_identity_and_text(self):
        body = {
            "kind": "engine",
            "type": "控制计划",
            "rules": [
                {"id": "keep", "category": "文件类型勾选审核", "priority": "P0", "rule_to_check": "文件类型勾选审核"},
            ],
        }
        frozen = freeze_rule_execution(body, DECLARATIONS)
        self.assertEqual(frozen["mode"], MODE_RULE_ITEMS)
        self.assertEqual(frozen["items"][0]["id"], "keep")
        self.assertEqual(frozen["items"][0]["rule_to_check"], "文件类型勾选审核")

    def test_items_beyond_the_available_checkers_are_not_fabricated_as_executed(self):
        items = [
            {"id": "full", "rule_to_check": "工序名称列必须包含\n iqc，否则不通过"},
            {"id": "duplicate", "rule_to_check": "过程流程图与控制计划列项及一致性审核"},
            {"id": "changed", "rule_to_check": "工序名称列必须包含 IQC，否则通过"},
        ]
        mapped = map_rule_items(items, DECLARATIONS)

        self.assertEqual(mapped[0]["checker_method"], "rule_1")
        self.assertEqual(mapped[0]["checker_index"], 0)
        self.assertEqual(mapped[0]["rule_to_check"], items[0]["rule_to_check"])
        self.assertEqual([item["coverage"] for item in mapped], [COVERAGE_MAPPED, COVERAGE_MAPPED, COVERAGE_UNSUPPORTED])
        self.assertEqual(mapped[2]["reason"], REASON_MANUAL_ONLY)

    def test_no_available_checker_is_reported_as_uncovered_without_inventing_a_binding(self):
        items = [{"id": "first", "rule_to_check": DECLARATIONS[0][1]}, {"id": "ambiguous", "rule_to_check": "shared requirement"}]
        mapped = map_rule_items(items, ())

        self.assertEqual(mapped[0]["coverage"], COVERAGE_UNSUPPORTED)
        self.assertEqual(mapped[1]["coverage"], COVERAGE_UNSUPPORTED)
        self.assertNotIn("checker_method", mapped[1])

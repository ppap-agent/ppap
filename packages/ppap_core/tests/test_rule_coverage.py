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
    def test_maps_exact_text_and_leaves_custom_item_unchecked(self):
        items = [
            {"id": "keep", "category": "文件", "priority": "P1", "rule_to_check": "文件类型勾选审核"},
            {"id": "extra", "category": "customer-only", "priority": None, "rule_to_check": "arbitrary customer requirement"},
        ]
        mapped = map_rule_items(items, DECLARATIONS)
        self.assertEqual(mapped[0]["coverage"], COVERAGE_MAPPED)
        self.assertEqual(mapped[0]["checker_method"], "rule_1")
        self.assertEqual(mapped[0]["checker_index"], 0)
        self.assertEqual(mapped[1]["coverage"], COVERAGE_UNSUPPORTED)
        self.assertEqual(mapped[1]["reason"], REASON_MANUAL_ONLY)

    def test_does_not_map_by_position_alone(self):
        items = [{"id": "1", "category": "基本信息", "priority": "P0", "rule_to_check": "必须填写零件号"}]
        mapped = map_rule_items(items, DECLARATIONS)
        self.assertEqual(mapped[0]["coverage"], COVERAGE_UNSUPPORTED)

    def test_matching_category_does_not_confirm_empty_or_different_rule_text(self):
        items = [
            {"id": "empty", "category": "文件类型勾选审核", "rule_to_check": ""},
            {"id": "different", "category": "文件类型勾选审核", "rule_to_check": "必须勾选文件类型"},
        ]
        mapped = map_rule_items(items, DECLARATIONS)
        self.assertEqual([item["coverage"] for item in mapped], [COVERAGE_UNSUPPORTED] * 2)
        self.assertTrue(all(item["reason"] == REASON_MANUAL_ONLY for item in mapped))
        self.assertTrue(all("checker_method" not in item for item in mapped))

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

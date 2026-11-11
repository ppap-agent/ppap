"""Real V3 dimension sample plus the human-oracle clause, assembled through core and the engine."""

import json
import unittest

from audit.audits import RoundInput, RoundWork
from corpus_contracts.core_round import bind_suite, leaf_suite, run_suite_round
from corpus_contracts.oracle import original


class DimensionProjectionTests(unittest.TestCase):
    def test_v3_dimension_product_projection_uses_confirmed_order_and_keeps_original_text(self):
        file_name = "尺寸检验报告-example.xlsm"
        asset = original(file_name)
        self.assertTrue(asset.is_file(), asset)
        customer_text = (
            "识别“CoverSheet”页是否存在，如果不存在则不通过，否则继续识别“CoverSheet”页中是否包含"
            "“零件号”、“零件名”、“项目”、“项目阶段”、“日期”是否存在，如果不存在则不通过，否则继续识别；"
            "当“零件号”、“零件名”、“项目”、“项目阶段”、“日期”内容均不为空则通过，否则不通过；"
        )
        source = leaf_suite("nio", "全功能检验报告")
        declared_text = json.loads(source.files["rules.json"])["rules"][0]["rule_to_check"]
        body = {
            "kind": "engine",
            "type": "尺寸检验报告",
            "rule_profile": "v3",
            "rules": [
                {"id": "declared-1", "category": "内置规则", "priority": "P0", "rule_to_check": declared_text},
                {"id": "unconfirmed-1", "category": "客户规则", "priority": "P0", "rule_to_check": customer_text},
            ],
        }
        suite = bind_suite(source, body, {"declared-1": "1", "unconfirmed-1": "2"})
        self.assertEqual(set(suite.entrypoints), {"declared-1", "unconfirmed-1"})
        target = RoundInput(
            "target",
            "file-1",
            "version-1",
            file_name,
            "ready",
            rule_version_id="rule-1",
            rule_body=body,
        )
        result = run_suite_round(
            RoundWork("round-1", "tenant-1", [target]),
            {"version-1": asset.read_bytes()},
            suites={"version-1": suite},
        )
        self.assertEqual([row["item_key"] for row in result["findings"]], ["version-1:rule:declared-1", "version-1:rule:unconfirmed-1"])
        self.assertEqual(result["findings"][1]["item"], customer_text)
        self.assertTrue(all(row["status"] in {"PASS", "NG"} for row in result["findings"]))
        self.assertEqual(result["coverage"]["checked"], [file_name])
        self.assertEqual(
            [(row["id"], row["coverage"]) for row in result["coverage"]["rule_items"]],
            [("declared-1", "executed"), ("unconfirmed-1", "executed")],
        )
        self.assertFalse(result["coverage"]["unchecked"])

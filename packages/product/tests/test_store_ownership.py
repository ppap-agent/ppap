import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from product import build_product_store
from product.store import AuthStore


class ProductStoreOwnershipTests(unittest.TestCase):
    def test_store_constructs_without_api_worker_engine_or_deep_ownership(self):
        objects = object()
        store = AuthStore(SimpleNamespace(), objects=objects, role="worker")
        self.assertIs(store.objects, objects)
        self.assertEqual(store.role, "worker")
        self.assertFalse(hasattr(store, "rule_execution_factory"))

    def test_composition_does_not_inject_a_parallel_engine(self):
        objects = object()
        with patch("audit.engine.checkers.rule_declarations.freeze_target_rule_execution", side_effect=AssertionError("legacy route")):
            store = build_product_store(SimpleNamespace(), objects=objects, role="worker")
        self.assertIs(store.objects, objects)
        self.assertEqual(store.role, "worker")
        self.assertFalse(hasattr(store, "rule_execution_factory"))

    def test_authorized_rule_metadata_waits_for_the_adopted_suite_binding(self):
        store = build_product_store(SimpleNamespace(), objects=object())
        rule_id = str(uuid4())
        body = {"type": "PFMEA", "rule_profile": "v3", "rules": [{"id": "custom", "rule_to_check": "原件要求"}]}
        prepared = [{"role": "target", "original_filename": "PFMEA.xlsx", "file_version_id": "original"}]
        principal = SimpleNamespace(tenant_id="tenant")
        with (
            patch.object(store, "_prepared_items", return_value=prepared),
            patch.object(store, "_rule_for_tenant", return_value=(rule_id, "用户原文", body)) as authorized_rule,
            patch("audit.engine.checkers.rule_declarations.freeze_target_rule_execution", side_effect=AssertionError("legacy route")),
        ):
            result = store._bound_audit_items(None, principal, [{"rule_version_id": rule_id}])
        self.assertEqual(str(authorized_rule.call_args.args[1]), rule_id)
        self.assertEqual(authorized_rule.call_args.args[2], principal.tenant_id)
        self.assertEqual(result[0]["rule_body"], body)
        self.assertEqual(result[0]["rule_version_id"], rule_id)
        self.assertEqual(result[0]["rule_title"], "用户原文")
        self.assertIsNone(result[0]["rule_execution"])

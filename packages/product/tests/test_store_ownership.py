import unittest
from types import SimpleNamespace

from product.store import AuthStore


class ProductStoreOwnershipTests(unittest.TestCase):
    def test_store_constructs_without_api_worker_engine_or_deep_ownership(self):
        objects = object()
        store = AuthStore(SimpleNamespace(), objects=objects, role="worker")
        self.assertIs(store.objects, objects)
        self.assertEqual(store.role, "worker")
        self.assertIsNone(store.rule_execution_factory)

    def test_composition_capability_is_explicitly_injected(self):
        marker = object()
        store = AuthStore(
            SimpleNamespace(),
            objects=object(),
            rule_execution_factory=marker,
        )
        self.assertIs(store.rule_execution_factory, marker)

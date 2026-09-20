import unittest
from types import SimpleNamespace
import tests.pathsetup  # noqa: F401
from ppap_worker import _rule_input


class BuiltinBindingTests(unittest.TestCase):
    def test_uploaded_text_does_not_change_checker_input(self):
        item = SimpleNamespace(rule_version_id='rule1', filename='PFMEA.xlsx', rule_body={
            'kind': 'engine', 'type': 'PFMEA', 'rule_profile': 'v3',
            'rules': [{'id': '99', 'category': 'customer-only', 'rule_to_check': 'arbitrary customer requirement'}]})
        first = _rule_input(item)
        self.assertTrue(first.rules)
        self.assertNotIn('customer-only', str(first.rules))
        item.rule_body['rules'] = []
        self.assertEqual(first.rules, _rule_input(item).rules)
        item.filename = 'PFMEA.pdf'
        self.assertEqual(first.rules, _rule_input(item).rules)

    def test_unsupported_checker_does_not_execute_customer_rules(self):
        item = SimpleNamespace(rule_version_id='rule1', filename='unknown.bin', rule_body={
            'kind': 'engine', 'type': 'unknown', 'rules': [{'rule_to_check': 'custom'}]})
        with self.assertRaisesRegex(ValueError, 'builtin_rules_unavailable'):
            _rule_input(item)

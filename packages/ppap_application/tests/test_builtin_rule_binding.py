import unittest
from types import SimpleNamespace


from ppap_core.audits import RoundInput, findings_from_rule_execution
from ppap_application import builtin_rule_input
from ppap_core.rule_coverage import COVERAGE_UNSUPPORTED, MODE_RULE_ITEMS


class BuiltinBindingTests(unittest.TestCase):
    def test_uploaded_text_does_not_change_checker_input(self):
        item = SimpleNamespace(rule_version_id='rule1', filename='PFMEA.xlsx', rule_body={
            'kind': 'engine', 'type': 'PFMEA', 'rule_profile': 'v3',
            'rules': [{'id': '99', 'category': 'customer-only', 'rule_to_check': 'arbitrary customer requirement'}]},
            rule_execution=None)
        first = builtin_rule_input(item)
        self.assertTrue(first.rules)
        self.assertNotIn('customer-only', str(first.rules))
        item.rule_body['rules'] = []
        self.assertEqual(first.rules, builtin_rule_input(item).rules)
        item.filename = 'PFMEA.pdf'
        self.assertEqual(first.rules, builtin_rule_input(item).rules)

    def test_unsupported_document_type_does_not_execute_customer_rules(self):
        from ppap_excel_audit_engine.checkers.rule_declarations import freeze_target_rule_execution
        body = {'kind': 'engine', 'type': 'unknown', 'rules': [{'id': '1', 'category': 'custom', 'priority': None, 'rule_to_check': 'custom', 'type_of_rule': 'text'}]}
        frozen = freeze_target_rule_execution(body, 'unknown.bin')
        self.assertEqual(frozen['mode'], MODE_RULE_ITEMS)
        self.assertEqual(frozen['items'][0]['coverage'], COVERAGE_UNSUPPORTED)
        projected = findings_from_rule_execution([], target=RoundInput('target', 'f1', 'v1', 'unknown.bin', 'ready', rule_body=body, rule_execution=frozen), execution=frozen)
        self.assertEqual(projected['findings'], [])
        self.assertTrue(any('custom' in row for row in projected['coverage']['unchecked']))

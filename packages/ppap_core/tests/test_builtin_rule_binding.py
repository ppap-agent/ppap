import unittest
from types import SimpleNamespace


from ppap_core.audits import RoundInput, RoundWork, findings_from_rule_execution
from ppap_core.engine_round import builtin_rule_input, run_round_from_bytes
from ppap_core.rule_coverage import COVERAGE_MAPPED, COVERAGE_UNSUPPORTED, MODE_RULE_ITEMS


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

    def test_v3_dimension_product_projection_keeps_unconfirmed_rule_unchecked(self):
        from ppap_excel_audit_engine.checkers.rule_declarations import declarations_for_target, freeze_target_rule_execution
        from tests.corpus.v3_poc.poc_oracle import load_poc_cases

        case = next(row for row in load_poc_cases() if row.file_name == '尺寸检验报告-example.xlsm')
        declared_text = declarations_for_target('尺寸检验报告', case.file_name, 'v3')[0][1]
        # The human oracle clause is not a confirmed binding to checker 1.
        customer_text = case.expected[1].rule_desc
        body = {
            'kind': 'engine', 'type': '尺寸检验报告', 'rule_profile': 'v3',
            'rules': [
                {'id': 'declared-1', 'category': '内置规则', 'priority': 'P0', 'rule_to_check': declared_text},
                {'id': 'unconfirmed-1', 'category': '客户规则', 'priority': 'P0', 'rule_to_check': customer_text},
            ],
        }
        execution = freeze_target_rule_execution(body, case.file_name)
        self.assertEqual([row['coverage'] for row in execution['items']], [COVERAGE_MAPPED, COVERAGE_UNSUPPORTED])
        target = RoundInput(
            'target', 'file-1', 'version-1', case.file_name, 'ready',
            rule_version_id='rule-1', rule_body=body, rule_execution=execution,
        )
        result = run_round_from_bytes(
            RoundWork('round-1', 'tenant-1', [target]),
            {'version-1': case.file_path.read_bytes()},
        )
        self.assertEqual([(row['item_key'], row['status']) for row in result['findings']], [('version-1:rule:declared-1', 'NG')])
        self.assertEqual(result['coverage']['checked'], [case.file_name])
        self.assertEqual(
            [(row['id'], row['coverage']) for row in result['coverage']['rule_items']],
            [('declared-1', COVERAGE_MAPPED), ('unconfirmed-1', COVERAGE_UNSUPPORTED)],
        )
        self.assertTrue(any(customer_text in note for note in result['coverage']['unchecked']))

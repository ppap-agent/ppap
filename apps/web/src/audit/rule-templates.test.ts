import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi'
import type {RegisteredCustomer} from './customer-register.ts'
import {currentRules, customerRules, ruleCoverage, ruleTemplateKey, templateName} from './rule-templates.ts'

type Rule = components['schemas']['Rule']
const rule = (id: string, customers: string[], type: string, extra: Partial<Rule> = {}) =>
  ({id, title: `${type}审核规则`, kind: 'engine', created_at: '2026-11-01', predecessor_id: null, customer_scope: 'restricted', type, rule_profile: 'v4', customers, models: [], parts: [], clause_count: 4, ...extra}) as Rule
const customer = (id: string, key: string, name: string, types: [string, string][] | null, rules: string[] = []): RegisteredCustomer => ({
  customer_id: id, key, name,
  coverage: types && {version: 'V3.33', effective_from: '2026-11-18', types: types.map(([code, rule_type]) => ({code, rule_type})), template_types: [], rule_types: rules},
})
const typeNames = {T11: '控制计划', T09: '过程流程图'}

test('each registered customer lists the current rules of every type in its M, and only those', () => {
  const nio = customer('nio', '蔚来', '蔚来/Nio', [['T11', '控制计划'], ['T09', '过程流程图']], ['T11'])
  const rules = [
    rule('cp-old', ['蔚来'], '控制计划'), rule('cp', ['蔚来'], '控制计划', {predecessor_id: 'cp-old'}), rule('cp-2', ['蔚来'], '控制计划', {title: '第二条控制计划规则'}),
    // A type outside M, a name that is not the key (even the shown name) and a non-engine rule are not listed.
    rule('dv', ['蔚来'], '零部件设计验证计划和报告'), rule('look', ['蔚来/Nio'], '过程流程图'), rule('other', ['蔚来'], '过程流程图', {kind: 'note'}),
  ]
  const [listed] = customerRules([nio], typeNames, rules)
  assert.deepEqual(listed.types?.map(type => [type.label, type.rules.map(row => row.id)]), [['控制计划', ['cp-2', 'cp']], ['过程流程图', []]])
  // The same rules in force serve the confirm step's rule picker.
  assert.deepEqual(currentRules(rules).map(row => row.id), ['cp', 'cp-2', 'dv', 'look'])
})

test('rule coverage N_r/M is the backend count of types in M with a current rule; nothing without a customer or M', () => {
  assert.deepEqual(ruleCoverage(customer('nio', '蔚来', '蔚来/Nio', [['T11', '控制计划'], ['T09', '过程流程图']], ['T11'])), {covered: 1, total: 2})
  assert.equal(ruleCoverage(customer('li', '理想', '理想/Li Auto', null)), null)
  assert.equal(ruleCoverage(undefined), null)
})

test('a customer without M lists no types', () => {
  const missing = customer('li', '理想', '理想/Li Auto', null)
  assert.deepEqual(customerRules([missing], typeNames, [rule('cp', ['理想'], '控制计划')]), [{customer: missing, types: null}])
})

test('the confirm step rule picker keys follow the customer field', () => {
  // A customer's built-in rules are named by its registered name.
  assert.equal(templateName('system:理想', [customer('li', '理想', '理想/Li Auto', null)]), '理想/Li Auto 规则')
  assert.equal(ruleTemplateKey({rule_profile: 'v4', customers: ['理想'], customer_scope: 'restricted'}), 'system:理想')
  assert.equal(ruleTemplateKey({rule_profile: 'v3', customers: [], customer_scope: 'all'}), 'shared')
  assert.equal(ruleTemplateKey({rule_profile: 'v3', customers: ['海岸线'], customer_scope: 'restricted'}), 'customer:海岸线')
  assert.equal(ruleTemplateKey({rule_profile: 'v3', customers: [], customer_scope: 'unconfirmed'}), 'pending')
})

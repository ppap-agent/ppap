import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi'
import {customerCoverage, sameCustomer} from './customer-coverage.ts'
import {buildTemplates, coverage} from './rule-templates.ts'

type Rule = components['schemas']['Rule']
const rule = (id: string, type: string, customers: string[] | null, extra: Partial<Rule> = {}) =>
  ({id, title: `${type}规则`, kind: 'engine', created_at: '', predecessor_id: null, customer_scope: 'restricted', type, rule_profile: 'v4', customers, models: [], parts: [], ...extra}) as Rule
const types = ['过程流程图', '控制计划', '潜在过程失效模式及后果分析']
const catalog = {suites: [], predefined: []}

test('a picker name and a rule name for the same customer match; different customers do not', () => {
  assert.equal(sameCustomer('理想', '理想汽车'), true)
  assert.equal(sameCustomer('UAES', 'UAES（联电）'), true)
  assert.equal(sameCustomer('蔚来', '理想汽车'), false)
  assert.equal(sameCustomer('', '理想'), false)
})

test('the new-audit picker shows the rules page coverage for that customer', () => {
  const rules = [rule('a', '过程流程图', ['理想汽车']), rule('b', '控制计划', ['理想汽车']), rule('c', '控制计划', ['蔚来汽车'])]
  const template = buildTemplates({rules, library: [], catalog, standardTypes: types}).find(row => sameCustomer(row.name, '理想'))!
  const expected = coverage(template)
  assert.equal(expected.known, true)
  assert.deepEqual(customerCoverage({rules, types, catalog}, '理想'), {supported: expected.effective, total: expected.total})
})

test('no coverage is reported for the automatic customer, an unread checker catalog or a customer without rules', () => {
  const rules = [rule('a', '控制计划', ['理想'])]
  assert.equal(customerCoverage({rules, types, catalog}, 'auto'), null)
  assert.equal(customerCoverage({rules, types, catalog: null}, '理想'), null)
  assert.equal(customerCoverage(null, '理想'), null)
  assert.equal(customerCoverage({rules, types, catalog}, '长城'), null)
})

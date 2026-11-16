import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi'
import {customerCoverage, customerFileSlots, sameCustomer} from './customer-coverage.ts'
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

test('the upload hint lists that customer\'s rule types', () => {
  const rules = [rule('a', '控制计划', ['理想汽车']), rule('b', '过程流程图', ['理想汽车']), rule('c', 'E07', ['UAES'])]
  const li = customerFileSlots({rules, types, catalog}, '理想')
  assert.deepEqual(li, [
    {type: '过程流程图', label: '过程流程图'},
    {type: '控制计划', label: '控制计划'},
  ])
  assert.deepEqual(customerFileSlots({rules, types, catalog: null}, 'UAES'), [{type: 'E07', label: '生产件样品照片'}])
  assert.equal(customerFileSlots({rules, types, catalog}, 'auto'), null)
  assert.equal(customerFileSlots(null, '理想'), null)
  assert.equal(customerFileSlots({rules, types, catalog}, '长城'), null)
})

test('without preset rules in the tenant list, the customer\'s file types and coverage come from the product checker catalog', () => {
  // The isolated-dev and fresh-tenant shape: /rules lists only the tenant's own rule; /suites lists the product leaves.
  const standard = ['全功能检验报告', '控制计划', '过程特殊特性清单', 'DV报告_PV报告_试验报告', '零部件设计验证计划和报告']
  const own = {...rule('own', '资料就绪', []), rule_profile: undefined, suite_key: 'custom:own'} as Rule
  const leaf = (path: string, title: string, extra: Partial<components['schemas']['SuitePredefined']> = {}) =>
    ({path, title, availability: 'installed', suite_key: `${{li: '理想', nio: '蔚来', uaes: 'UAES'}[path.split('/')[0]]}:${path.split('/')[1]}`, revision_id: path, clause_count: 2, evidence_reader_clause_ids: ['1', '2'], ...extra}) as components['schemas']['SuitePredefined']
  const catalog = {suites: [], predefined: [
    leaf('li/L01', '01 全功能检验报告'), leaf('li/L03', '02 零部件设计验证报告（DV报告_PV报告_试验报告）'), leaf('li/L08', '10 控制计划', {evidence_reader_clause_ids: []}),
    leaf('nio/N01', '1-2规则-测量报告'), leaf('nio/N04', '11规则-特殊特性清单'), {path: 'nio/N05', title: '8规则-过程流程图', availability: 'unseeded'} as components['schemas']['SuitePredefined'],
    leaf('uaes/U01', '初始样品检验报告审核规则'), {path: 'old/X01', title: '旧目录', availability: 'historical'} as components['schemas']['SuitePredefined'],
  ]}
  const source = {rules: [own], types: standard, catalog}
  assert.deepEqual(customerFileSlots(source, '理想'), [
    {type: '全功能检验报告', label: '全功能检验报告'},
    {type: '控制计划', label: '控制计划'},
    {type: 'DV报告_PV报告_试验报告', label: 'DV报告_PV报告_试验报告'},
  ])
  // A leaf with no evidence reader leaves its clauses to people; it is not counted as supported.
  assert.deepEqual(customerCoverage(source, '理想汽车'), {supported: 2, total: 3})
  // An unseeded leaf of the same directory still names a type; a title naming no standard type keeps its own words.
  assert.deepEqual(customerFileSlots(source, '蔚来')?.map(slot => slot.label), ['过程特殊特性清单', '测量报告', '过程流程图'])
  assert.deepEqual(customerCoverage(source, '蔚来'), {supported: 2, total: 3})
  assert.deepEqual(customerFileSlots(source, 'UAES'), [{type: 'E01', label: '初始样品检验报告'}])
  assert.equal(customerFileSlots(source, '长城'), null)
  assert.equal(customerFileSlots({...source, catalog: null}, '理想'), null)
  // Preset rules in the tenant list remain the source when present.
  assert.deepEqual(customerFileSlots({...source, rules: [own, rule('a', '控制计划', ['理想'])]}, '理想'), [{type: '控制计划', label: '控制计划'}])
})

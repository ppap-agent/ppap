import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi'
import type {RequirementDirectoryCollection, RequirementDirectoryVersion} from '../journey/requirement-directory'
import {buildTemplates, coverage, enterpriseTemplateKey, pendingRows, ruleTemplateKey, templateName, typeCode, typeGroups} from './rule-templates.ts'

type Rule = components['schemas']['Rule']
type Catalog = components['schemas']['SuiteCatalog']
const standard = ['控制计划', 'PFMEA', '过程流程图']
const preset = (id: string, customer: string, type: string, key: string, extra: Partial<Rule> = {}) =>
  ({id, title: `${type}审核规则`, kind: 'engine', created_at: '', predecessor_id: null, customer_scope: 'restricted', type, rule_profile: 'v4', customers: [customer], models: [], parts: [], preset_key: key, clause_count: 4, ...extra}) as Rule
const version = (id: string, scope: Partial<RequirementDirectoryVersion['scope']>, extra: Partial<RequirementDirectoryVersion> = {}): RequirementDirectoryVersion =>
  ({id, title: `${id} 规则`, version: 'v1', scope: {customer: '', part: '', revision: '', allCustomers: false, allParts: true, allRevisions: true, ...scope}, at: '2026-11-01', baseId: null, clauseCount: 3, documentType: '控制计划', documentTypeAliases: [], suiteKey: null, sourceReview: 'checked', ...extra})
const collection = (latest: RequirementDirectoryVersion, draft = false): RequirementDirectoryCollection => draft ? {id: latest.id, versions: [], draft: latest} : {id: latest.id, versions: [latest], draft: null}
const summary = (key: string, extra: Partial<Catalog['suites'][number]> = {}) =>
  ({suite_key: key, title: key, origin: 'official', selection_mode: 'automatic_official', current_revision_id: 'r', verification_status: 'verified', rule_update_pending: false, official_update_available: false, program_missing: false, ...extra}) as Catalog['suites'][number]

test('presets form one system template per customer and say what each file type can do on its own', () => {
  const catalog: Catalog = {
    suites: [summary('UAES:U08', {rule_update_pending: true})],
    predefined: [
      {path: 'uaes/U01', title: 'PFMEA', availability: 'installed', suite_key: 'UAES:U01', revision_id: 'a', clause_count: 4, evidence_reader_clause_ids: ['1', '2', '3', '4']},
      {path: 'uaes/U02', title: '流程图', availability: 'installed', suite_key: 'UAES:U02', revision_id: 'b', clause_count: 4, evidence_reader_clause_ids: ['1']},
    ],
  }
  const [uaes] = buildTemplates({rules: [preset('cp', 'UAES', '控制计划', 'U08'), preset('pf', 'UAES', 'PFMEA', 'U01'), preset('pfd', 'UAES', '过程流程图', 'U02'), preset('ph', 'UAES', 'E07', 'U09', {title: '生产件样品照片审核规则'})], library: [], catalog, standardTypes: standard})
  assert.equal(uaes.name, 'UAES 规则')
  assert.deepEqual(uaes.rows.map(row => [row.label, row.automation.state, row.automation.note ?? '']), [
    ['控制计划', 'stale', ''], ['PFMEA', 'auto', ''], ['过程流程图', 'partial', '4 条中 1 条有检查'], ['生产件样品照片', 'manual', '审核时由人判断'],
  ])
  // A rule update or a missing program leaves the type out of the coverage until the program is ready again.
  assert.deepEqual(coverage(uaes), {effective: 2, total: 4, known: true})
  assert.deepEqual(pendingRows(uaes).map(row => row.type), ['控制计划'])
  assert.deepEqual(uaes.rows[0].target, {kind: 'system', id: 'cp'})
})

test('uploaded rules form templates by customer; shared and unconfirmed rules have their own', () => {
  const templates = buildTemplates({
    rules: [],
    library: [collection(version('a', {customer: '海岸线'})), collection(version('b', {allCustomers: true}, {suiteKey: 'custom:b'})), collection(version('c', {}), true)],
    catalog: {suites: [summary('custom:b', {origin: 'enterprise', verification_status: 'unverified'})]},
    standardTypes: standard,
  })
  assert.deepEqual(templates.map(template => [template.name, template.source]), [['通用规则', 'enterprise'], ['海岸线', 'enterprise'], ['待整理', 'enterprise']])
  assert.deepEqual(templates.map(template => template.rows[0].automation.state), ['unverified', 'manual', 'manual'])
  const [offered] = buildTemplates({rules: [preset('cp', 'UAES', '控制计划', 'U08')], library: [], catalog: {suites: [summary('UAES:U08', {selection_mode: 'candidate_only', current_revision_id: null, verification_status: 'unverified'})]}, standardTypes: standard})
  assert.equal(offered.rows[0].automation.state, 'inactive')
  assert.deepEqual(coverage(offered), {effective: 0, total: 1, known: true})
  // An uploaded rule whose program was never generated is not offered for audit, whatever its verification says.
  const [starter] = buildTemplates({rules: [], library: [collection(version('d', {allCustomers: true}, {suiteKey: 'custom:d'}))], catalog: {suites: [summary('custom:d', {origin: 'enterprise', verification_status: 'unverified', program_missing: true})]}, standardTypes: standard})
  assert.deepEqual([starter.rows[0].automation.state, starter.rows[0].automation.note], ['manual', '检查程序由平台离线准备，准备好后才能用于审核'])
  assert.deepEqual(coverage(starter), {effective: 0, total: 1, known: true})
  assert.equal(templates[2].rows[0].draft, true)
  assert.deepEqual(templates[1].rows[0].target, {kind: 'requirement', id: 'a'})
})

test('a template never claims what the unread checker catalog has not said', () => {
  const [template] = buildTemplates({rules: [preset('cp', '理想', '控制计划', 'L08')], library: [], catalog: null, standardTypes: standard})
  assert.equal(template.rows[0].automation.state, 'unknown')
  assert.equal(coverage(template).known, false)
  assert.deepEqual(template.missing, ['PFMEA', '过程流程图'])
  const [failed] = buildTemplates({rules: [preset('cp', '理想', '控制计划', 'L08')], library: [], catalog: 'failed', standardTypes: standard})
  assert.equal(failed.rows[0].automation.state, 'unreadable')
})

test('rules of one file type share a group; the group counts once and carries the product code', () => {
  const library = [collection(version('a', {customer: '海岸线'}, {suiteKey: 'custom:a'})), collection(version('b', {customer: '海岸线'}, {suiteKey: 'custom:b'})), collection(version('c', {customer: '海岸线'}, {documentType: 'PFMEA'}))]
  const [template] = buildTemplates({rules: [], library, catalog: {suites: [summary('custom:a', {origin: 'enterprise'}), summary('custom:b', {origin: 'enterprise', program_missing: true})]}, standardTypes: standard})
  assert.deepEqual(typeGroups(template).map(group => [group.type, group.rows.length]), [['控制计划', 2], ['PFMEA', 1]])
  assert.deepEqual(coverage(template), {effective: 1, total: 2, known: true})
  assert.equal(template.rows[0].updatedAt, '2026-11-01')
  assert.deepEqual([typeCode('控制计划'), typeCode('全功能检验报告'), typeCode('E07'), typeCode('设计失效模式及后果分析')], ['T11', 'T01', 'E07', ''])
})

test('template keys and names follow the customer field', () => {
  const scope = {customer: ' 蔚来 ', part: '', revision: '', allCustomers: false, allParts: true, allRevisions: true}
  assert.equal(enterpriseTemplateKey(scope), 'customer:蔚来')
  assert.equal(enterpriseTemplateKey({...scope, allCustomers: true}), 'shared')
  assert.equal(enterpriseTemplateKey({...scope, customer: ''}), 'pending')
  assert.equal(templateName('system:理想'), '理想 规则')
  assert.equal(ruleTemplateKey({rule_profile: 'v4', customers: ['理想'], customer_scope: 'restricted'}), 'system:理想')
  assert.equal(ruleTemplateKey({rule_profile: 'v3', customers: [], customer_scope: 'all'}), 'shared')
  assert.equal(ruleTemplateKey({rule_profile: 'v3', customers: ['海岸线'], customer_scope: 'restricted'}), 'customer:海岸线')
  assert.equal(ruleTemplateKey({rule_profile: 'v3', customers: [], customer_scope: 'unconfirmed'}), 'pending')
})

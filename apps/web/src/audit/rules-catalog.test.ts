import assert from 'node:assert/strict'
import test from 'node:test'
import {buildRuleCatalog, matchesCatalog, officialCatalogTitle, type CatalogRow} from './rules-catalog.ts'

const rows: CatalogRow[] = [
  {key: 'rule', title: '控制计划 · 理想', source: '已发布规则', sourceKind: 'publishedRule', detail: '控制计划 · 理想', action: '核对规则', href: '1', ruleVersionId: '1', suiteKey: null, documentType: '控制计划'},
  {key: 'official', title: '控制计划 · UAES', source: '系统提供', sourceKind: 'officialSuite', detail: 'v5/UAES/U08', action: '查看规则', href: 'v5/UAES/U08', ruleVersionId: null, suiteKey: null, documentType: ''},
]

test('one search finds a published rule and an official checker without merging them', () => {
  assert.deepEqual(rows.filter(row => matchesCatalog(row, '控制计划')).map(row => row.key), ['rule', 'official'])
  assert.equal(rows.find(row => matchesCatalog(row, 'uaes'))?.source, '系统提供')
  assert.notEqual(rows[0].href, rows[1].href)
})

test('the catalog keeps separate identities and hides only a proven suite link', () => {
  const catalog = buildRuleCatalog({
    rules: [
      {id: 'rule-1', title: '控制计划', documentType: '控制计划', customer: '理想', part: 'P-1', suiteKey: 'custom:rule-1'},
      {id: 'rule-2', title: '控制计划', documentType: 'Control plan', customer: 'UAES', part: '', suiteKey: null},
    ],
    official: [{path: 'v5/UAES/U08', title: '控制计划审核规则（CP）'}],
    enterprise: [
      {suiteKey: 'custom:rule-1', title: '控制计划', ready: true},
      {suiteKey: 'custom:other', title: '控制计划', ready: false},
    ],
  })
  assert.deepEqual(catalog.map(row => row.key), ['published:rule-1', 'published:rule-2', 'official:v5/UAES/U08', 'enterprise:custom:other'])
  assert.equal(catalog.find(row => row.key === 'published:rule-1')?.suiteKey, 'custom:rule-1')
  assert.equal(catalog.find(row => row.key === 'enterprise:custom:other')?.action, '检查功能尚待完善')
  const official = catalog.find(row => row.key === 'official:v5/UAES/U08')
  assert.equal(official?.title, '控制计划 · UAES')
  assert.equal(official?.source, '系统提供')
  assert.equal(official?.ruleVersionId, null)
  assert.equal(official?.detail, '')
  assert.equal(matchesCatalog(official!, 'cp'), true)
  assert.equal(matchesCatalog(official!, 'u08'), true)
  assert.equal(matchesCatalog(official!, 'v5/uaes/u08'), true)
  const enterprise = catalog.find(row => row.key === 'enterprise:custom:other')
  assert.equal(enterprise?.detail, '')
  assert.equal(matchesCatalog(enterprise!, 'custom:other'), true)
  assert.deepEqual(officialCatalogTitle('not-a-suite-path', '控制计划'), {title: '控制计划', aliases: '控制计划'})
  const flow = officialCatalogTitle('v5/理想/L11', '08 过程流程图')
  assert.equal(flow.title, '过程流程图 · 理想')
  assert.equal(flow.aliases.includes('08 过程流程图'), true)
  assert.equal(flow.aliases.includes('L11'), true)
  assert.equal(catalog.filter(row => matchesCatalog(row, 'control plan')).map(row => row.ruleVersionId).join(','), 'rule-2')
  assert.deepEqual(catalog.filter(row => matchesCatalog(row, 'P-1')).map(row => row.key), ['published:rule-1'])
  assert.equal(new Set(catalog.map(row => row.sourceKind)).size, 3)
})

import assert from 'node:assert/strict'
import test from 'node:test'
import {clearsRequirementQueryOnEscape, groupRequirementDirectory, publishedLibraryAfterRead, publishedRuleAction, requirementDirectory, requirementHaystack, requirementQueryHref, requirementSearchKeyAction} from './requirement-directory.ts'
import {demoBasis} from './requirements-state.ts'
import {scopeLabel, type RequirementCollection, type RequirementScope} from './requirement-library-state.ts'

const scope = {customer: '', part: '', revision: '', allCustomers: true, allParts: true, allRevisions: true}

test('the requirement directory groups explicit predecessors and retains all immutable version identities', () => {
  const directory = groupRequirementDirectory([
    {id: 'v2', title: '新名称', version: 'v2', scope, reason: '修订', clause_count: 27, created_at: '2026-10-02', predecessor_id: 'v1', document_type: '控制计划', document_type_aliases: ['控制计划', 'Control plan'], suite_key: null, source_review: 'unchecked'},
    {id: 'other', title: '相同范围的独立规则', version: 'v1', scope, reason: '', clause_count: 3, created_at: '2026-10-01', predecessor_id: null, document_type: '全尺寸', document_type_aliases: ['全尺寸'], suite_key: 'custom:other', source_review: 'partial'},
    {id: 'v1', title: '原名称', version: 'v1', scope, reason: '', clause_count: 26, created_at: '2026-10-01', predecessor_id: null, document_type: '控制计划', document_type_aliases: ['控制计划', 'Control plan'], suite_key: null, source_review: 'checked'},
  ])
  assert.equal(directory.length, 2)
  assert.deepEqual(directory.find(row => row.id === 'v1')!.versions.map(row => row.id), ['v1', 'v2'])
  assert.equal(directory.find(row => row.id === 'v1')!.versions.at(-1)!.clauseCount, 27)
  assert.equal(directory.find(row => row.id === 'other')!.versions[0].documentType, '全尺寸')
  assert.equal(directory.find(row => row.id === 'other')!.versions[0].suiteKey, 'custom:other')
  assert.equal(requirementHaystack('控制计划规则', '客户', '控制计划', ['控制计划', 'Control plan']).includes('control plan'), true)
  assert.equal(requirementHaystack('控制计划规则', '客户', '全尺寸').includes('全尺寸'), true)
  assert.equal(requirementHaystack('控制计划规则', '客户', '控制计划').includes('全尺寸'), false)
  assert.equal(JSON.stringify(directory).includes('basis'), false)
  assert.equal(publishedRuleAction({suiteKey: null, sourceReview: 'checked'}), '检查功能尚待完善')
  assert.equal(publishedRuleAction({suiteKey: 'custom:1', sourceReview: 'partial'}), '继续确认')
  assert.equal(publishedRuleAction({suiteKey: 'custom:1', sourceReview: 'checked'}), '核对规则')
  assert.equal(publishedRuleAction({suiteKey: 'custom:1', sourceReview: 'unchecked'}), '核对出处')
})

test('a part number matches one rule and search does not push history', () => {
  const part = (value: string): RequirementScope => ({customer: 'UAES', part: value, revision: '', allCustomers: false, allParts: false, allRevisions: true})
  assert.equal(requirementHaystack('控制计划', scopeLabel(part('P-1')), '控制计划').includes('p-1'), true)
  assert.equal(requirementHaystack('控制计划', scopeLabel(part('P-2')), '控制计划').includes('p-1'), false)
  assert.equal(requirementQueryHref('https://ppap.example/requirements?requirement=1', 'P-1'), '/requirements?requirement=1&reqQuery=P-1')
  assert.equal(requirementQueryHref('https://ppap.example/requirements?requirement=1&reqQuery=P-1', ''), '/requirements?requirement=1')
  assert.equal(new URL('https://ppap.example' + requirementQueryHref('https://ppap.example/requirements', 'y'.repeat(250))).searchParams.get('reqQuery')!.length, 200)
  assert.equal(clearsRequirementQueryOnEscape('P-1', 'Escape'), true)
  assert.equal(clearsRequirementQueryOnEscape('', 'Escape'), false)
  assert.equal(clearsRequirementQueryOnEscape('P-1', 'Enter'), false)
  assert.equal(requirementSearchKeyAction('Escape', 'P-1'), 'clear')
  assert.equal(requirementSearchKeyAction('Enter', 'P-1'), 'open')
  assert.equal(requirementSearchKeyAction('Tab', 'P-1'), 'none')
  assert.equal(requirementSearchKeyAction('Escape', ''), 'none')
})

test('directory refresh preserves local drafts and lets server metadata replace cached published labels', () => {
  const collection: RequirementCollection = {id: 'v1', versions: [{id: 'v1', basis: demoBasis(), scope, reason: '', at: '2026-10-01', baseId: null}], draft: {id: 'draft', basis: {...demoBasis(), source: {...demoBasis().source, title: '尚未保存的修改'}}, scope, reason: '本地原因', baseId: 'v1'}}
  const before = structuredClone(collection)
  const remote = groupRequirementDirectory([{id: 'v1', title: '服务端名称', version: 'v1', scope, reason: '', clause_count: 9, created_at: '2026-10-01', predecessor_id: null, document_type: '控制计划', document_type_aliases: ['控制计划', 'Control plan'], suite_key: null, source_review: 'checked'}])
  const result = requirementDirectory([collection], remote)
  assert.equal(result[0].versions[0].title, '服务端名称')
  assert.equal(result[0].versions[0].clauseCount, 9)
  assert.deepEqual(result[0].versions[0].documentTypeAliases, ['控制计划', 'Control plan'])
  assert.equal(requirementHaystack(result[0].versions[0].title, '', result[0].versions[0].documentType, result[0].versions[0].documentTypeAliases).includes('control plan'), true)
  assert.equal(result[0].draft!.title, '尚未保存的修改')
  assert.deepEqual(collection, before)
})


test('a failed rule list does not keep the previous customer rules', () => {
  const versions = [{id: 'v1', title: '控制计划', version: 'v1', scope, reason: '', clause_count: 1, created_at: '2026-10-01', predecessor_id: null, document_type: '控制计划', document_type_aliases: ['Control plan'], suite_key: null, source_review: 'unchecked' as const}]
  const shown = publishedLibraryAfterRead(false, versions)
  assert.equal(shown.versions.length, 1)
  assert.equal(shown.groups.length, 1)
  const failed = publishedLibraryAfterRead(true, versions)
  assert.deepEqual(failed.versions, [])
  assert.deepEqual(failed.groups, [])
})

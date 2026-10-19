import assert from 'node:assert/strict'
import {test} from 'node:test'
import {localBasis} from './requirements-state.ts'
import {libraryDraftIssues} from './requirement-library-state.ts'
import {restoreRequirementSources, verifiedClauseCount} from './requirement-sources.ts'

const unit = {id: 'docx:1:2:2', location: '表 1 · 行 2 · 列 2', excerpt: '必须填写零件号'}
function draft() {
  const basis = localBasis('规则.docx')
  return {id: 'draft', versions: [], draft: {id: 'draft', baseId: null, scope: {customer: '', part: '', revision: '', allCustomers: true, allParts: true, allRevisions: true}, reason: '用户导入', basis: {...basis,
    source: {...basis.source, state: 'ready' as const, version: 'v1'},
    server: {body: {kind: 'engine' as const, type: '控制计划', rule_profile: 'v3', customers: [], models: [], parts: [], rules: []}, material: {file_id: 'f1', file_version_id: 'v1', original_filename: '规则.docx', role: 'reference' as const, rule_version_id: null}},
    clauses: [{id: '1', title: '零件号', text: unit.excerpt, original: unit.excerpt, source: '规则.docx', location: '条款 1（原件位置待核对）', checked: true, applicability: 'pending' as const, reason: ''}],
  }}}
}
test('old checked draft recovers exact original coordinates without replacing user fields or checks', () => {
  const collection = draft(), basis = collection.draft.basis
  assert.equal(verifiedClauseCount(basis), 0)
  const restored = restoreRequirementSources(basis, {file_version_id: 'v1', units: [unit]})
  assert.equal(restored.clauses[0].location, unit.location)
  assert.equal(restored.clauses[0].checked, true)
  assert.equal(restored.source, basis.source)
  assert.equal(restored.server, basis.server)
  const loaded = {...restored, server: {...restored.server!, sources: {file_version_id: 'v1', units: [unit]}}}
  assert.equal(verifiedClauseCount(loaded), 1)
  assert.deepEqual(libraryDraftIssues({...collection, draft: {...collection.draft, basis: loaded}}), [])
  assert.equal(verifiedClauseCount({...loaded, server: {...loaded.server, sources: {file_version_id: 'v1', units: []}}}), 0)
  assert.equal(restoreRequirementSources(restored, {file_version_id: 'v1', units: [unit]}), restored)
  assert.equal(restoreRequirementSources(basis, {file_version_id: 'v2', units: [unit]}), basis)
})
test('editing placeholder or checking boxes cannot replace provenance; ambiguous text is not guessed', () => {
  const collection = draft(), basis = collection.draft.basis
  basis.clauses[0].location = '已核对原件第 1 条'
  assert.equal(verifiedClauseCount(basis), 0)
  assert.ok(libraryDraftIssues(collection).some(issue => issue.includes('出处')))
  assert.equal(restoreRequirementSources(basis, {file_version_id: 'v1', units: [unit, {...unit, id: 'other'}]}), basis)
})

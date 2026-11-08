import assert from 'node:assert/strict'
import test from 'node:test'
import {checkerPublicationAddress, checkerSelectionAddress, checkedWorkspaceRevision, optimizationSelection, readCheckerWorkspace, refreshKeepsEditor, sameRevisionTenant} from './checker-workspace-contract.ts'
import type {CheckerRevision} from './checker-contract.ts'

const scope = 'round-a:file-a:clause-a:suite-a', legacyScope = 'round-a:file-a:clause-a'
const original = new URL('https://ppap.example/audit?round=round-a&file=file-a&clause=clause-a#basis')
const select = (url: URL, revisionId: string, operationId: string | null = null) => checkerSelectionAddress(url, scope, {revisionId, operationId, purpose: 'publish', explicit: true})
const publication = {revision_id: 'D', expected_revision: 2, operation_id: 'publish-D', force_unverified: true}

test('two manual saves retain one workspace identity and restore D rather than historical E1/current E2', () => {
  const c = select(original, 'C'), d = select(c, 'D')
  assert.equal(c.searchParams.get('checkerDetailScope'), scope)
  assert.equal(d.searchParams.get('checkerDetailScope'), scope)
  assert.equal(d.searchParams.has('checkerDetailRoot'), false)
  assert.equal(readCheckerWorkspace(d, scope, 'E1').revisionId, 'D')
  assert.equal(readCheckerWorkspace(d, scope, 'E2').revisionId, 'D')
  assert.equal(readCheckerWorkspace(d, scope, 'E1').explicit, true)
  assert.equal(d.searchParams.get('round'), 'round-a')
  assert.equal(d.hash, '#basis')
})

test('unknown D publication survives refresh even when the server write never executed', () => {
  const d = select(select(original, 'C'), 'D')
  const pending = checkerPublicationAddress(d, scope, publication)
  for (const source of ['E1', 'E2', 'new-optimizer-candidate']) {
    const restored = readCheckerWorkspace(pending, scope, source)
    assert.equal(restored.revisionId, 'D')
    assert.deepEqual(restored.pending, publication)
    assert.equal(optimizationSelection(restored, {operationId: 'new-operation', revisionId: 'new-optimizer-candidate', explicit: true}, Boolean(restored.pending)), null)
  }
  assert.throws(() => select(pending, 'E2'), /原发布/)
  assert.throws(() => checkerPublicationAddress(pending, scope, {...publication, operation_id: 'another-publication'}), /原发布/)
  assert.deepEqual(readCheckerWorkspace(checkerPublicationAddress(pending, 'other-scope', null), scope, 'E1').pending, publication)
  const done = readCheckerWorkspace(checkerPublicationAddress(pending, scope, null), scope, 'E1')
  assert.equal(done.pending, null)
  assert.equal(done.revisionId, 'D')
})

test('selection, action and pending publication cannot leak across workspace scopes', () => {
  let url = checkerSelectionAddress(original, scope, {revisionId: 'D', operationId: 'old-operation', purpose: 'rollback', explicit: true})
  url = checkerPublicationAddress(url, scope, publication)
  const other = readCheckerWorkspace(url, 'round-b:file-a:clause-a:suite-a', 'other-source')
  assert.equal(other.revisionId, 'other-source')
  assert.equal(other.purpose, 'publish')
  assert.equal(other.operationId, null)
  assert.equal(other.pending, null)
  assert.equal(other.explicit, false)
  for (const changes of [{id: 'other-revision', suite_key: 'suite-a'}, {id: 'D', suite_key: 'other-suite'}]) assert.throws(() => checkedWorkspaceRevision(changes as CheckerRevision, 'suite-a', 'D'), /工作区/)
})

test('optimizer rehydration keeps subsequent manual saves, but a new operation selects its actual candidate', () => {
  const candidate = optimizationSelection(readCheckerWorkspace(original, scope, 'E1'), {operationId: 'optimizer-1', revisionId: 'B', explicit: false})!
  let url = checkerSelectionAddress(original, scope, candidate)
  url = select(select(url, 'C', 'optimizer-1'), 'D', 'optimizer-1')
  const restored = readCheckerWorkspace(url, scope, 'E1')
  assert.equal(optimizationSelection(restored, {operationId: 'optimizer-1', revisionId: 'B', explicit: true}), null)
  assert.deepEqual(optimizationSelection(restored, {operationId: 'optimizer-2', revisionId: 'E', explicit: true}), {revisionId: 'E', operationId: 'optimizer-2', purpose: 'publish', explicit: true})
  assert.equal(optimizationSelection(restored, {operationId: 'optimizer-2', revisionId: 'E', explicit: true}, true), null)
  // During acceptance/failure the operation has no candidate, so selection remains D.
  assert.equal(readCheckerWorkspace(url, scope, 'E2').revisionId, 'D')
})

test('pending edit preserves E2 when enterprise head advances to E3 and blocks switching versions', () => {
  const url = new URL(original)
  url.searchParams.set('checkerEditScope', scope)
  url.searchParams.set('checkerEditBase', 'E2')
  url.searchParams.set('checkerEditOperation', 'save-C')
  const restored = readCheckerWorkspace(url, scope, 'E3')
  assert.equal(restored.revisionId, 'E2')
  assert.equal(restored.explicit, true)
  assert.deepEqual(restored.editPending, {base: 'E2', operationId: 'save-C'})
  assert.throws(() => select(url, 'E3'), /原保存/)
  assert.equal(readCheckerWorkspace(url, 'other-scope', 'E3').editPending, null)
})

test('old scoped URLs retain their selection and pending write until exact suite authorization allows migration', () => {
  const old = new URL(original)
  for (const [key, value] of Object.entries({checkerFeedbackScope: legacyScope, checkerDetailRoot: 'C', checkerDetailRevision: 'D', checkerDetailAction: 'rollback', checkerOptimization: 'optimizer-1', checkerPublish: publication.operation_id, checkerPublishRevision: 'D', checkerPublishExpected: '2', checkerPublishUnverified: '1'})) old.searchParams.set(key, value)
  const restored = readCheckerWorkspace(old, scope, 'E1', legacyScope)
  assert.equal(restored.legacy, true)
  assert.equal(restored.revisionId, 'D')
  assert.equal(restored.purpose, 'rollback')
  assert.equal(restored.operationId, 'optimizer-1')
  assert.deepEqual(restored.pending, publication)
  checkedWorkspaceRevision({id: 'D', suite_key: 'suite-a'} as CheckerRevision, 'suite-a', restored.revisionId)
  const migrated = checkerPublicationAddress(checkerSelectionAddress(old, scope, restored), scope, publication)
  assert.deepEqual(readCheckerWorkspace(migrated, scope, 'E1').pending, publication)
  assert.equal(readCheckerWorkspace(migrated, scope, 'E1').legacy, false)
  assert.equal(readCheckerWorkspace(old, 'other-scope', 'E3', 'other-legacy-scope').legacyBlocked, true)
  assert.equal(old.searchParams.get('checkerPublish'), publication.operation_id)
})

test('legacy pending edit with no selected candidate restores its exact base before migration', () => {
  const url = new URL(original)
  for (const [key, value] of Object.entries({checkerFeedbackScope: legacyScope, checkerEditBase: 'E2', checkerEditOperation: 'save-C'})) url.searchParams.set(key, value)
  const restored = readCheckerWorkspace(url, scope, 'E3', legacyScope)
  assert.equal(restored.legacy, true)
  assert.equal(restored.revisionId, 'E2')
  assert.equal(restored.explicit, true)
  assert.deepEqual(restored.editPending, {base: 'E2', operationId: 'save-C'})
  assert.equal(readCheckerWorkspace(url, 'other', 'E3', 'other').legacyBlocked, true)
})


test('auto-discovered optimization cannot replace saved D, while choosing that same operation can', () => {
  const auto = {operationId: 'optimizer-2', revisionId: 'E', explicit: false}
  for (const priorOperation of [null, 'optimizer-1']) {
    const restored = readCheckerWorkspace(select(original, 'D', priorOperation), scope, 'E1')
    assert.equal(optimizationSelection(restored, auto), null)
    assert.deepEqual(optimizationSelection(restored, {...auto, explicit: true}), {revisionId: 'E', operationId: 'optimizer-2', purpose: 'publish', explicit: true})
  }
})

test('feedback refresh keeps only the same scope, suite, and frozen revision editor', () => {
  const shown = {scope: 'round-a:file-a:clause-a:suite-a', suite: 'suite-a', id: 'revision-a', tenant: 'tenant-a'}
  assert.equal(refreshKeepsEditor(shown, {scope: shown.scope, suite: shown.suite, id: shown.id}), true)
  assert.equal(refreshKeepsEditor(null, {scope: shown.scope, suite: shown.suite, id: shown.id}), false)
  assert.equal(refreshKeepsEditor(shown, {scope: 'other-scope', suite: shown.suite, id: shown.id}), false)
  assert.equal(refreshKeepsEditor(shown, {scope: shown.scope, suite: 'other-suite', id: shown.id}), false)
  assert.equal(refreshKeepsEditor(shown, {scope: shown.scope, suite: shown.suite, id: 'revision-b'}), false)
  assert.equal(sameRevisionTenant(shown, {id: shown.id, tenant_id: 'tenant-a'}), true)
  assert.equal(sameRevisionTenant(shown, {id: shown.id, tenant_id: null}), false)
  assert.equal(sameRevisionTenant(shown, {id: shown.id}), false)
  assert.equal(sameRevisionTenant({id: shown.id, tenant: null}, {id: shown.id, tenant_id: null}), true)
  assert.equal(sameRevisionTenant({id: shown.id, tenant: null}, {id: shown.id}), true)
  assert.equal(sameRevisionTenant(shown, {id: 'revision-official', tenant_id: null}), true)
  assert.equal(sameRevisionTenant(null, {id: shown.id, tenant_id: 'tenant-b'}), true)
})

test('recovering pending edit C retains it despite an auto-discovered optimization', () => {
  const url = new URL(original)
  url.searchParams.set('checkerEditScope', scope)
  url.searchParams.set('checkerEditBase', 'E2')
  url.searchParams.set('checkerEditOperation', 'save-C')
  const pending = readCheckerWorkspace(url, scope, 'E3')
  const auto = {operationId: 'optimizer-2', revisionId: 'E', explicit: false}
  assert.equal(optimizationSelection(pending, auto, Boolean(pending.editPending)), null)
  for (const key of ['checkerEditScope', 'checkerEditBase', 'checkerEditOperation']) url.searchParams.delete(key)
  const recovered = readCheckerWorkspace(select(url, 'C', pending.operationId), scope, 'E3')
  assert.equal(optimizationSelection(recovered, auto), null)
  assert.deepEqual(optimizationSelection(recovered, {...auto, explicit: true}), {revisionId: 'E', operationId: 'optimizer-2', purpose: 'publish', explicit: true})
})

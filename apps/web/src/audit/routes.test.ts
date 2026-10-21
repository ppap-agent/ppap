import assert from 'node:assert/strict'
import test from 'node:test'
import {canonicalAddress, readAuditRoute, requestAddress, surfaceAddress, taskAddress, usesLegacyJourney} from './routes.ts'

test('natural task address retains file, clause and report context', () => {
  const url = new URL('https://ppap.test/audit?task=abc&file=f1&clause=c1&report=r1')
  assert.equal(canonicalAddress(url), '/tasks/abc?file=f1&clause=c1&report=r1')
  assert.deepEqual(readAuditRoute(new URL('https://ppap.test/tasks/abc?file=f1')), {surface: 'audit', taskId: 'abc'})
})

test('archive and rules aliases retain exact detail and historical version', () => {
  assert.equal(canonicalAddress(new URL('https://ppap.test/audit?task=t&view=archive&archiveDoc=d&auditRef=frozen')), '/files?task=t&archiveDoc=d&auditRef=frozen')
  assert.equal(canonicalAddress(new URL('https://ppap.test/?view=requirements&requirement=r&reqVersion=draft')), '/requirements?requirement=r&reqVersion=draft')
})

test('old draft, round, review and share addresses are not rewritten as new tasks', () => {
  for (const search of ['?case=draft&stage=decision&clause=c', '?round=r&resume=changes', '?draft=d', '?share=s&report=p']) {
    assert.equal(canonicalAddress(new URL('https://ppap.test/' + search)), '/' + search)
  }
})

test('old round links enter the unified reader unless they carry legacy stage context', () => {
  for (const path of ['/?round=r', '/audit?round=r', '/?case=d&round=r', '/audit?round=r&clause=c', '/?round=r&clause=c&focus=review']) assert.equal(usesLegacyJourney(new URL(path, 'https://ppap.test')), false)
  for (const path of ['/?case=d', '/?draft=d', '/?round=r&stage=decision', '/?round=r&resume=changes', '/?round=r&clause=c', '/dev']) assert.equal(usesLegacyJourney(new URL(path, 'https://ppap.test')), true)
  assert.equal(usesLegacyJourney(new URL('https://ppap.test/?share=s&case=d')), false)
  assert.equal(canonicalAddress(new URL('https://ppap.test/?round=r')), '/audit?round=r')
})

test('home and new task are distinct; navigation does not carry unrelated detail', () => {
  assert.equal(canonicalAddress(new URL('https://ppap.test/')), '/workbench')
  assert.equal(canonicalAddress(new URL('https://ppap.test/audit-files?q=steel')), '/workbench?q=steel')
  assert.equal(taskAddress('t1'), '/tasks/t1')
  assert.equal(taskAddress(null), '/tasks/new')
  assert.equal(surfaceAddress('workbench'), '/workbench')
  assert.equal(surfaceAddress('archive'), '/files')
  assert.equal(readAuditRoute(new URL('https://ppap.test/tasks/new')).taskId, null)
})

test('lost command recovery keeps task and request identities through refresh and canonicalization', () => {
  const prepared = requestAddress(new URL('https://ppap.test/tasks/new?fromRound=old'), 'new-task', 'recognitionRequest', 'request-a')
  assert.equal(prepared, '/tasks/new-task?fromRound=old&recognitionRequest=request-a')
  const confirmation = requestAddress(new URL('https://ppap.test/audit?task=t'), 't', 'acceptRequest', 'request-b')
  assert.equal(canonicalAddress(new URL(confirmation, 'https://ppap.test')), '/tasks/t?acceptRequest=request-b')
})

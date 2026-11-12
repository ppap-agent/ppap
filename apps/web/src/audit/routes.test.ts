import assert from 'node:assert/strict'
import test from 'node:test'
import {auditAddressChanged, canonicalAddress, readAuditRoute, requestAddress, surfaceAddress, taskAddress, writeAuditAddress} from './routes.ts'

test('local address edits retain history identity and notify without a synthetic back/forward event', () => {
  const descriptors = ['window', 'history'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)] as const)
  const writes: unknown[][] = [], events: string[] = [], draft = {auditUploadDraft: 'draft-b'}
  Object.defineProperty(globalThis, 'history', {configurable: true, value: {state: draft, pushState: (...args: unknown[]) => writes.push(['push', ...args]), replaceState: (...args: unknown[]) => writes.push(['replace', ...args])}})
  Object.defineProperty(globalThis, 'window', {configurable: true, value: {dispatchEvent: (event: Event) => {events.push(event.type); assert.equal(writes.length, events.length)}}})
  try {
    writeAuditAddress('/tasks/new', 'push')
    writeAuditAddress('/tasks/a?file=f&clause=c&report=r&packageRequest=p')
    assert.deepEqual(writes, [['push', draft, '', '/tasks/new'], ['replace', draft, '', '/tasks/a?file=f&clause=c&report=r&packageRequest=p']])
    assert.deepEqual(events, [auditAddressChanged, auditAddressChanged])
  } finally {
    for (const [key, descriptor] of descriptors) {if (descriptor) Object.defineProperty(globalThis, key, descriptor); else Reflect.deleteProperty(globalThis, key)}
  }
})

test('natural task address retains file, clause and report context', () => {
  const url = new URL('https://ppap.test/audit?task=abc&file=f1&clause=c1&report=r1')
  assert.equal(canonicalAddress(url), '/tasks/abc?file=f1&clause=c1&report=r1')
  assert.deepEqual(readAuditRoute(new URL('https://ppap.test/tasks/abc?file=f1')), {surface: 'audit', taskId: 'abc'})
})

test('archive and rules aliases retain exact detail and historical version', () => {
  assert.equal(canonicalAddress(new URL('https://ppap.test/audit?task=t&view=archive&archiveDoc=d&auditRef=frozen')), '/files?task=t&archiveDoc=d&auditRef=frozen')
  assert.equal(canonicalAddress(new URL('https://ppap.test/?view=requirements&requirement=r&reqVersion=draft')), '/requirements?requirement=r&reqVersion=draft')
})

test('retired first-generation addresses land on PPAP Audit; Round links keep their target; shares stay as they are', () => {
  assert.equal(canonicalAddress(new URL('https://ppap.test/?share=s&report=p')), '/?share=s&report=p')
  for (const search of ['?round=r', '?case=d&round=r', '?round=r&stage=decision', '?round=r&resume=changes']) assert.equal(canonicalAddress(new URL('https://ppap.test/' + search)), '/tasks/new?round=r')
  // A Round link keeps the exact file and clause; the page opens that Round's task.
  assert.equal(canonicalAddress(new URL('https://ppap.test/audit?round=r&file=f&clause=c&basis=1')), '/tasks/new?round=r&file=f&clause=c&basis=1')
  for (const path of ['/?case=d', '/?draft=d&stage=decision', '/dev']) assert.equal(canonicalAddress(new URL(path, 'https://ppap.test')), '/tasks/new')
  assert.equal(canonicalAddress(new URL('https://ppap.test/rules')), '/requirements')
})

test('home opens PPAP Audit; navigation does not carry unrelated detail', () => {
  for (const path of ['/', '/?view=workbench']) assert.equal(canonicalAddress(new URL(path, 'https://ppap.test')), '/tasks/new')
  assert.equal(canonicalAddress(new URL('https://ppap.test/audit-files?q=steel')), '/tasks/new?q=steel')
  assert.equal(taskAddress('t1'), '/tasks/t1')
  assert.equal(taskAddress(null), '/tasks/new')
  assert.equal(surfaceAddress('archive'), '/files')
  assert.equal(readAuditRoute(new URL('https://ppap.test/tasks/new')).taskId, null)
})

test('lost command recovery keeps task and request identities through refresh and canonicalization', () => {
  const prepared = requestAddress(new URL('https://ppap.test/tasks/new?fromRound=old'), 'new-task', 'recognitionRequest', 'request-a')
  assert.equal(prepared, '/tasks/new-task?fromRound=old&recognitionRequest=request-a')
  const confirmation = requestAddress(new URL('https://ppap.test/audit?task=t'), 't', 'acceptRequest', 'request-b')
  assert.equal(canonicalAddress(new URL(confirmation, 'https://ppap.test')), '/tasks/t?acceptRequest=request-b')
})

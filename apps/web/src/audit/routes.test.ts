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

test('retired first-generation addresses land on their current pages; shares stay as they are', () => {
  assert.equal(canonicalAddress(new URL('https://ppap.test/?share=s&report=p')), '/?share=s&report=p')
  for (const search of ['?round=r', '?case=d&round=r', '?round=r&stage=decision', '?round=r&resume=changes']) assert.equal(canonicalAddress(new URL('https://ppap.test/' + search)), '/audit?round=r')
  assert.equal(canonicalAddress(new URL('https://ppap.test/?round=r&clause=c&focus=review')), '/audit?round=r&clause=c&focus=review')
  for (const path of ['/?case=d', '/?draft=d&stage=decision', '/dev']) assert.equal(canonicalAddress(new URL(path, 'https://ppap.test')), '/workbench')
  assert.equal(canonicalAddress(new URL('https://ppap.test/rules')), '/requirements')
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


test('a precheck readback identity survives canonical task navigation', () => {
  const prepared = requestAddress(new URL('https://ppap.test/tasks/t'), 't', 'precheckRequest', 'request-c')
  assert.equal(prepared, '/tasks/t?precheckRequest=request-c')
  assert.equal(canonicalAddress(new URL(prepared, 'https://ppap.test')), prepared)
})

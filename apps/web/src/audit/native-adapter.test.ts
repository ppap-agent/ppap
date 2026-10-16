import assert from 'node:assert/strict'
import test, {afterEach} from 'node:test'
import type {components} from '../generated/openapi.ts'
import {createNativeAuditAdapter} from './native-adapter.ts'

type Task = components['schemas']['AuditTask']
const fetchBefore = globalThis.fetch
Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})
const file: Task['files'][number] = {file: {id: 'version-a', file_id: 'file-a', original_filename: 'a.xlsx', content_type: 'application/octet-stream', byte_size: 3, status: 'ready', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: 'pending', reasons: []}, match: null}
const task: Task = {id: 'task-a', number: 'PPAP-20261016-001', state: 'pending_confirm', analysis_mode: 'standard', created_at: '2026-10-16T00:00:00Z', updated_at: '2026-10-16T00:00:00Z', source_draft_id: 'task-a', round_id: null, customer_preference: null, completion_reason: null, estimated_points: 0, files: [file], input_revision: 2}
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}})
afterEach(() => {globalThis.fetch = fetchBefore})

test('native precheck reads the original validation after a lost submit reply without resubmitting', async () => {
  const calls: {url: string; method: string}[] = []
  let validationId = ''
  globalThis.fetch = async (input, init) => {
    const url = String(input), method = init?.method || 'GET'
    calls.push({url, method})
    if (url.endsWith('/validation')) return json({validation: null})
    if (method === 'POST') {
      const body = JSON.parse(String(init?.body))
      validationId = body.request_id
      assert.equal(body.source_file_id, 'file-a')
      assert.equal(body.source_file_version_id, 'version-a')
      throw new TypeError('reply lost')
    }
    assert.ok(validationId)
    assert.equal(url, `/file-validations/${validationId}`)
    return json({id: validationId, source_file_id: 'file-a', source_file_version_id: 'version-a', status: 'completed', result: {state: 'valid', reasons: []}})
  }
  const adapter = createNativeAuditAdapter({deepOffered: false, customers: []})
  await assert.rejects(adapter.readValidation(file), /reply lost/)
  assert.deepEqual(await adapter.readValidation(file), {validation_id: validationId, validation: {state: 'valid', reasons: []}})
  assert.equal(calls.filter(call => call.method === 'POST').length, 1)
})

test('native precheck refuses a result for another validation request', async () => {
  globalThis.fetch = async () => json({id: 'another-request', source_file_id: 'file-a', source_file_version_id: 'version-a', status: 'completed', result: {state: 'valid', reasons: []}})
  const adapter = createNativeAuditAdapter({deepOffered: false, customers: []})
  await assert.rejects(adapter.readValidation({...file, validation_id: 'original-request'}), /回执/)
})

test('native matching sends all provenance and its exact input revision without a manual rule override', async () => {
  const removed = {...file, file: {...file.file, id: 'version-b'}, validation_id: 'validation-b', selection: 'removed' as const, removal_reason: '文件加密'}
  const rows = [{...file, validation_id: 'validation-a'}, removed]
  globalThis.fetch = async (url, init) => {
    assert.equal(String(url), '/audit-tasks')
    assert.equal(init?.method, 'POST')
    assert.deepEqual(JSON.parse(String(init?.body)), {request_id: 'recognition-a', task_id: 'task-a', expected_revision: 2, analysis_mode: 'standard', customer_preference: null, files: [{file_version_id: 'version-a', validation_id: 'validation-a', selection: 'selected', removal_reason: null}, {file_version_id: 'version-b', validation_id: 'validation-b', selection: 'removed', removal_reason: '文件加密'}]})
    return json({...task, files: rows, state: 'matching', input_revision: 3, recognition_request_id: 'recognition-a'})
  }
  const next = await createNativeAuditAdapter({deepOffered: false, customers: []}).match(rows, 'standard', null, task, 'recognition-a')
  assert.equal(next.id, 'task-a')
  assert.equal(next.input_revision, 3)
  assert.equal(next.files[1].selection, 'removed')
})

test('a lost native confirmation reply can only be read by its original request, not automatically posted again', async () => {
  const calls: string[] = []
  globalThis.fetch = async (url, init) => {
    calls.push(`${init?.method} ${url}`)
    if (init?.method === 'POST') {
      assert.deepEqual(JSON.parse(String(init.body)), {request_id: 'accept-a', expected_revision: 2})
      throw new TypeError('reply lost')
    }
    assert.equal(String(url), '/audit-tasks/task-a?request_id=accept-a')
    return json({...task, state: 'reviewing', round_id: 'round-a', acceptance: {request_id: 'accept-a', state: 'accepted', reason: null}})
  }
  const adapter = createNativeAuditAdapter({deepOffered: false, customers: []})
  await assert.rejects(adapter.confirm(task, 'accept-a'), /reply lost/)
  const recovered = await adapter.readTask('task-a', 'accept-a')
  assert.equal(recovered.round_id, 'round-a')
  assert.deepEqual(calls, ['POST /audit-tasks/task-a/confirm', 'GET /audit-tasks/task-a?request_id=accept-a'])
})

test('an explicit no-write confirmation refusal reads the current task and offers correction without another POST', async () => {
  const calls: string[] = []
  globalThis.fetch = async (url, init) => {
    calls.push(`${init?.method} ${url}`)
    if (init?.method === 'POST') return json({error: 'audit_task_charging_unavailable'}, 409)
    assert.equal(String(url), '/audit-tasks/task-a?request_id=accept-a')
    return json(task)
  }
  const adapter = createNativeAuditAdapter({deepOffered: false, customers: []})
  const refused = await adapter.confirm(task, 'accept-a')
  assert.deepEqual(refused.acceptance, {request_id: 'accept-a', state: 'rejected', reason: '当前环境尚未开放本次审核计费，审核没有开始。'})
  assert.equal(refused.round_id, null)
  assert.deepEqual(calls, ['POST /audit-tasks/task-a/confirm', 'GET /audit-tasks/task-a?request_id=accept-a'])
})

test('a refusal cannot overwrite an already accepted round found by readback', async () => {
  globalThis.fetch = async (_url, init) => init?.method === 'POST' ? json({error: 'task_input_locked'}, 409) : json({...task, state: 'reviewing', round_id: 'round-a', acceptance: {request_id: 'accept-original', state: 'accepted', reason: null}})
  const current = await createNativeAuditAdapter({deepOffered: false, customers: []}).confirm(task, 'accept-new')
  assert.equal(current.round_id, 'round-a')
  assert.deepEqual(current.acceptance, {request_id: 'accept-original', state: 'accepted', reason: null})
})

test('authorization, server and unknown refusals do not grant another confirmation', async () => {
  for (const [status, code] of [[401, 'unauthenticated'], [403, 'access_denied'], [500, 'audit_task_charging_unavailable'], [409, 'unrecognized_conflict']] as const) {
    const calls: string[] = []
    globalThis.fetch = async (url, init) => {calls.push(`${init?.method} ${url}`); return json({error: code}, status)}
    await assert.rejects(createNativeAuditAdapter({deepOffered: false, customers: []}).confirm(task, 'accept-a'))
    assert.deepEqual(calls, ['POST /audit-tasks/task-a/confirm'])
  }
})

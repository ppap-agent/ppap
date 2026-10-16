import assert from 'node:assert/strict'
import test, {afterEach} from 'node:test'
import type {components} from '../generated/openapi.ts'
import {createNativeAuditAdapter, nativeDisplayAdapter, nativeReportAdapter, nativeSupportAdapter} from './native-adapter.ts'
import {readReportPdf, readReportZip} from './report-contract.ts'
import {requestTrace} from '../api.ts'

type Task = components['schemas']['AuditTask']
const fetchBefore = globalThis.fetch
Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})
const file: Task['files'][number] = {file: {id: 'version-a', file_id: 'file-a', original_filename: 'a.xlsx', content_type: 'application/octet-stream', byte_size: 3, status: 'ready', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: 'pending', reasons: []}, match: null}
const task: Task = {id: 'task-a', number: 'PPAP-20261016-001', state: 'pending_confirm', analysis_mode: 'standard', created_at: '2026-10-16T00:00:00Z', updated_at: '2026-10-16T00:00:00Z', source_draft_id: 'task-a', round_id: null, customer_preference: null, completion_reason: null, estimated_points: 0, files: [file], input_revision: 2}
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}})
afterEach(() => {globalThis.fetch = fetchBefore})

test('native support reads authenticated configuration and only its displayed image version', async () => {
  const info: components['schemas']['AuditSupportInfo'] = {state: 'configured', config_version: 'config-a', phone: null, email: 'support@example.test', service_hours: '09:00–18:00', qr_available: true}
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), 'http://example.test')
    assert.equal(init?.method, 'GET')
    assert.equal(init?.credentials, 'include')
    if (url.pathname === '/support') return json(info)
    assert.equal(url.pathname, '/support/qr')
    assert.deepEqual(Object.fromEntries(url.searchParams), {config_version: 'config-a'})
    return new Response('qr-image', {headers: {'Content-Type': 'image/png'}})
  }
  const observed = await nativeSupportAdapter.read()
  assert.deepEqual(observed, info)
  const image = await nativeSupportAdapter.readQr(observed.config_version!)
  assert.equal(image.type, 'image/png')
  assert.equal(await image.text(), 'qr-image')
  globalThis.fetch = async () => json({error: 'support_configuration_changed'}, 409)
  await assert.rejects(nativeSupportAdapter.readQr(observed.config_version!))
})

test('native binary failures carry their own response trace without changing their frozen requests', async () => {
  const reference = {task_id: 'task-a', round_id: 'round-a', file_version_id: 'version-a', result_attempt: 2, report_id: 'report-a'}
  const artifact = {artifact_id: 'artifact-a', filename: 'a.pdf', content_type: 'application/pdf' as const, byte_size: 8}
  const job: components['schemas']['AuditReportPackage'] = {package_id: 'package-a', reports: [reference], receipt: {request_id: 'business-package', state: 'accepted', reason: null}, state: 'ready', artifact: {...artifact, content_type: 'application/zip', filename: 'a.zip'}, reason: null, retry_allowed: false}
  const reads = [() => nativeSupportAdapter.readQr('config-a'), () => nativeReportAdapter.readPdf!(reference, artifact), () => nativeReportAdapter.readPackageFile!(job)]
  for (const [index, read] of reads.entries()) {
    globalThis.fetch = async (_input, init) => {
      assert.equal(init?.method, 'GET')
      assert.equal(init?.credentials, 'include')
      return new Response('unavailable', {status: 503, headers: {'X-Request-ID': `binary-request-${index}`}})
    }
    await assert.rejects(read(), cause => requestTrace(cause) === `binary-request-${index}`)
  }
})

test('native PDF reads carry the full frozen reference and never create another audit or report', async () => {
  const reference = {task_id: 'task-a', round_id: 'round-a', file_version_id: 'version-a', result_attempt: 2, report_id: 'report-a'}
  const artifact = {artifact_id: 'artifact-a', filename: 'a.xlsx-审核报告.pdf', content_type: 'application/pdf' as const, byte_size: 8}
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), 'http://example.test')
    assert.equal(url.pathname, '/audit-tasks/task-a/reports/report-a/pdf')
    assert.deepEqual(Object.fromEntries(url.searchParams), {round_id: 'round-a', file_version_id: 'version-a', result_attempt: '2', artifact_id: 'artifact-a'})
    assert.equal(init?.method, 'GET')
    assert.equal(init?.credentials, 'include')
    return new Response('%PDF-1.7', {headers: {'Content-Type': 'application/pdf'}})
  }
  assert.equal(await (await readReportPdf(nativeReportAdapter, reference, artifact)).text(), '%PDF-1.7')
  globalThis.fetch = async () => json({error: 'access_denied'}, 403)
  await assert.rejects(readReportPdf(nativeReportAdapter, reference, artifact))
})

test('native ZIP uses one request identity for submission, readback and the exact artifact download', async () => {
  const reports = ['a', 'b'].map(id => ({task_id: 'task-a', round_id: 'round-a', file_version_id: `version-${id}`, result_attempt: 1, report_id: `report-${id}`}))
  const job: components['schemas']['AuditReportPackage'] = {package_id: 'package-a', reports, receipt: {request_id: 'request-a', state: 'accepted', reason: null}, state: 'ready', artifact: {artifact_id: 'artifact-a', filename: '审核报告.zip', content_type: 'application/zip', byte_size: 7}, reason: null, retry_allowed: false}
  const calls: string[] = []
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), 'http://example.test')
    calls.push(`${init?.method} ${url.pathname}`)
    if (url.pathname.endsWith('/zip')) {
      assert.deepEqual(Object.fromEntries(url.searchParams), {package_id: 'package-a', artifact_id: 'artifact-a'})
      assert.equal(init?.credentials, 'include')
      return new Response('PKbytes', {headers: {'Content-Type': 'application/zip'}})
    }
    if (init?.method === 'POST') {assert.deepEqual(JSON.parse(String(init.body)), {request_id: 'request-a', reports}); throw new TypeError('reply lost')}
    return json(job)
  }
  await assert.rejects(nativeReportAdapter.createPackage!(reports, 'request-a'), /reply lost/)
  assert.deepEqual(await nativeReportAdapter.readPackage!('request-a'), job)
  assert.equal(await (await readReportZip(nativeReportAdapter, job)).text(), 'PKbytes')
  assert.deepEqual(calls, ['POST /audit-report-packages', 'GET /audit-report-packages/request-a', 'GET /audit-report-packages/request-a/zip'])
  globalThis.fetch = async () => json(null)
  await assert.rejects(nativeReportAdapter.readPackage!('request-a'))
})

test('an explicit pre-acceptance ZIP size refusal is rejected, not an unknown accepted work', async () => {
  const reports = ['a', 'b'].map(id => ({task_id: 'task-a', round_id: 'round-a', file_version_id: `version-${id}`, result_attempt: 1, report_id: `report-${id}`}))
  globalThis.fetch = async () => json({error: 'report_package_too_large'}, 409)
  const rejected = await nativeReportAdapter.createPackage!(reports, 'request-a')
  assert.equal(rejected.receipt.state, 'rejected')
  assert.equal(rejected.receipt.request_id, 'request-a')
  assert.deepEqual(rejected.reports, reports)
  assert.equal(rejected.package_id, null)
  assert.match(rejected.reason!, /过大/)
})

test('a lost display-save reply reads only its original request and absence remains unknown', async () => {
  const command: components['schemas']['AuditFileDisplayRequest'] = {request_id: 'display-a', reference: {task_id: 'task-a', round_id: 'round-a', file_version_id: 'version-a'}, expected_revision: 2, values: {customer: 'A', model: null, part_number: 'P-1', part: '零件'}}
  const calls: string[] = []
  let written = false
  globalThis.fetch = async (url, init) => {
    calls.push(`${init?.method} ${url}`)
    if (init?.method === 'POST') {assert.deepEqual(JSON.parse(String(init.body)), command); throw new TypeError('reply lost')}
    return json(written ? {request: command, state: 'saved', info: {revision: 3, values: command.values}, reason: null} : null)
  }
  await assert.rejects(nativeDisplayAdapter.save(command), /reply lost/)
  assert.equal((await nativeDisplayAdapter.read(command)).state, 'unknown')
  written = true
  assert.deepEqual(await nativeDisplayAdapter.read(command), {request: command, state: 'saved', info: {revision: 3, values: command.values}, reason: null})
  assert.deepEqual(calls, ['POST /audit-tasks/task-a/display-info', 'GET /audit-tasks/task-a/display-info/display-a', 'GET /audit-tasks/task-a/display-info/display-a'])
})

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

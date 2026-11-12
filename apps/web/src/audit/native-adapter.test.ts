import assert from 'node:assert/strict'
import test, {afterEach} from 'node:test'
import type {components} from '../generated/openapi.ts'
import {createNativeAuditAdapter, nativeArchiveAdapter, nativeDisplayAdapter, nativeReportAdapter, nativeSharingAdapter, nativeSupportAdapter, nativeUsageAdapter, sharedReportAdapter, readSharedArchive, readAuditCustomers} from './native-adapter.ts'
import {readReportPdf, readReportZip} from './report-contract.ts'
import {requestTrace} from '../api.ts'

type Task = components['schemas']['AuditTask']
const fetchBefore = globalThis.fetch

test('known ZIP rejections stay distinct from lost acceptance replies', async () => {
  const reports = [1, 2].map(n => ({task_id: `task-${n}`, round_id: `round-${n}`, file_version_id: `version-${n}`, result_attempt: 1, report_id: `report-${n}`}))
  for (const [status, code] of [[400, 'invalid_report_package'], [409, 'report_not_ready'], [409, 'report_package_too_large']] as const) {
    globalThis.fetch = async () => new Response(JSON.stringify({error: code}), {status, headers: {'Content-Type': 'application/json'}})
    const result = await nativeReportAdapter.createPackage!(reports, 'original-request')
    assert.equal(result.receipt.state, 'rejected')
    assert.equal(result.receipt.request_id, 'original-request')
    assert.deepEqual(result.reports, reports)
  }
  globalThis.fetch = async () => {throw new TypeError('reply lost')}
  await assert.rejects(nativeReportAdapter.createPackage!(reports, 'original-request'))
  globalThis.fetch = async () => new Response(JSON.stringify({error: 'temporarily_unavailable'}), {status: 503, headers: {'Content-Type': 'application/json'}})
  await assert.rejects(nativeReportAdapter.createPackage!(reports, 'original-request'))
})
Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})
const file: Task['files'][number] = {file: {id: 'version-a', file_id: 'file-a', original_filename: 'a.xlsx', content_type: 'application/octet-stream', byte_size: 3, status: 'ready', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: 'pending', reasons: []}, match: null}
const task: Task = {id: 'task-a', number: 'PPAP-20261016-001', state: 'pending_confirm', analysis_mode: 'standard', created_at: '2026-10-16T00:00:00Z', updated_at: '2026-10-16T00:00:00Z', source_draft_id: 'task-a', round_id: null, customer_preference: null, completion_reason: null, estimated_points: 0, files: [file], input_revision: 2, confirmation_basis_digest: 'a'.repeat(64), confirmation_scope: null}
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}})
afterEach(() => {globalThis.fetch = fetchBefore})

test('native report sharing reads exact recipient attempts and never turns a read into another send', async () => {
  const calls: {method: string; path: string; params: URLSearchParams; body: unknown}[] = []
  const target = {id: 'ou_one', kind: 'user' as const}
  const input: components['schemas']['AuditReportShareRequest'] = {request_id: 'share-one', organization_id: 'tenant-one', reports: [], targets: [target], content: {kind: 'link', artifact: null, package_id: null}}
  globalThis.fetch = async (address, init) => {
    assert.equal(init?.credentials, 'include')
    const url = new URL(String(address), 'https://example.test')
    calls.push({method: init?.method || 'GET', path: url.pathname, params: url.searchParams, body: init?.body ? JSON.parse(String(init.body)) : null})
    return json({})
  }
  await nativeSharingAdapter.listTargets('张 & 王', 'user', 'next-page')
  await nativeSharingAdapter.send(input)
  const selected = [{target, attempt_id: 'old-attempt'}]
  await nativeSharingAdapter.read({request_id: input.request_id, attempts: selected})
  await nativeSharingAdapter.retryFailed(input.request_id, target, 'new-attempt')
  assert.deepEqual(calls.map(({method, path}) => [method, path]), [['GET', '/audit-share-targets'], ['POST', '/audit-report-shares'], ['GET', '/audit-report-shares/share-one'], ['POST', '/audit-report-shares/share-one/retries']])
  assert.equal(calls[0].params.get('query'), '张 & 王')
  assert.equal(calls[0].params.get('page_token'), 'next-page')
  assert.deepEqual(calls[1].body, input)
  assert.deepEqual(JSON.parse(calls[2].params.get('attempts')!), selected)
  assert.deepEqual(calls[3].body, {target, attempt_id: 'new-attempt'})
})

test('definite share rejection permits new composition but an in-flight conflict remains unknown', async () => {
  const input: components['schemas']['AuditReportShareRequest'] = {request_id: 'share-one', organization_id: 'tenant-one', reports: [], targets: [{id: 'ou_one', kind: 'user'}], content: {kind: 'link', artifact: null, package_id: null}}
  globalThis.fetch = async () => json({error: 'share_target_unverified'}, 409)
  const rejected = await nativeSharingAdapter.send(input)
  assert.equal(rejected.receipt.state, 'rejected')
  assert.deepEqual(rejected.request, input)
  assert.equal(rejected.deliveries[0].retry_allowed, false)
  globalThis.fetch = async () => json({error: 'share_request_pending'}, 409)
  await assert.rejects(nativeSharingAdapter.send(input))
  globalThis.fetch = async () => {throw new TypeError('lost response')}
  await assert.rejects(nativeSharingAdapter.send(input))
})

test('an ungranted directory reports organization authorization instead of a transient retry', async () => {
  globalThis.fetch = async () => json({error: 'share_directory_permission_missing'}, 503)
  await assert.rejects(nativeSharingAdapter.listTargets('', 'group'), {
    code: 'share_directory_permission_missing',
    status: 503,
  })
  globalThis.fetch = async () => json({error: 'share_directory_user_permission_missing'}, 503)
  await assert.rejects(nativeSharingAdapter.listTargets('张', 'user'), {
    code: 'share_directory_user_permission_missing',
    status: 503,
  })
})

test('directory fetch and body stalls expire without replay and keep the exact query for explicit recovery', async context => {
  context.mock.timers.enable({apis: ['setTimeout']})
  const page = {organization_id: 'tenant-one', organization_name: '当前组织', targets: [], next_page_token: null}
  for (const phase of ['fetch', 'body']) {
    const calls: string[] = [], signals: AbortSignal[] = []
    let finish!: (response: Response) => void
    const late = new Promise<Response>(resolve => {finish = resolve})
    globalThis.fetch = async (input, init) => {
      assert.equal(init?.method, 'GET'); assert.equal(init?.credentials, 'include')
      assert.ok(init?.signal); signals.push(init.signal); calls.push(String(input))
      return phase === 'fetch' ? late : {ok: true, json: () => late.then(response => response.json())} as unknown as Response
    }
    const first = nativeSharingAdapter.listTargets('质量 & 王工', 'group', 'page / two')
    const expired = assert.rejects(first, {name: 'TimeoutError', message: '读取组织目录超时，请重新读取目标。'})
    await Promise.resolve()
    context.mock.timers.tick(45_000)
    await expired
    assert.equal(calls.length, 1); assert.equal(signals[0].aborted, true)
    const original = calls[0]
    const params = new URL(original, 'https://example.test').searchParams
    assert.equal(params.get('query'), '质量 & 王工')
    assert.equal(params.get('kind'), 'group')
    assert.equal(params.get('page_token'), 'page / two')
    globalThis.fetch = async (input, init) => {
      assert.equal(String(input), original); assert.ok(init?.signal)
      signals.push(init.signal); calls.push(String(input)); return json(page)
    }
    assert.deepEqual(await nativeSharingAdapter.listTargets('质量 & 王工', 'group', 'page / two'), page)
    finish(json({...page, organization_id: 'late-organization'}))
    await assert.rejects(first, {name: 'TimeoutError'})
    context.mock.timers.tick(45_000)
    assert.equal(calls.length, 2); assert.equal(signals[1].aborted, false)
  }
})

test('a service directory deadline stays distinct from missing organization authorization', async () => {
  globalThis.fetch = async () => json({error: 'share_directory_timeout'}, 503)
  await assert.rejects(nativeSharingAdapter.listTargets('质量', 'user'), {
    code: 'share_directory_timeout', status: 503, message: '读取组织目录超时，请重新读取目标。',
  })
})

test('a shared report is read only from its frozen artifact and checked against its response digest', async () => {
  const body = new TextEncoder().encode('%PDF-frozen-report')
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', body)), byte => byte.toString(16).padStart(2, '0')).join('')
  const reference = {task_id: 'private-task', round_id: 'round-one', file_version_id: 'version-one', result_attempt: 1, report_id: 'report-one'}
  const artifact = {artifact_id: 'artifact-one', filename: '报告.pdf', content_type: 'application/pdf' as const, byte_size: body.length}
  let responseDigest = digest
  const urls: string[] = []
  globalThis.fetch = async (url, init) => {assert.equal(init?.credentials, 'include'); urls.push(String(url)); return new Response(body, {headers: {'Content-Type': 'application/pdf', 'X-Content-SHA256': responseDigest}})}
  const adapter = sharedReportAdapter('share-one')
  assert.equal(await (await adapter.readPdf(reference, artifact)).text(), '%PDF-frozen-report')
  const url = new URL(urls[0], 'https://example.test')
  assert.equal(url.pathname, '/audit-report-shares/share-one/pdf')
  assert.equal(url.searchParams.get('report_id'), reference.report_id)
  assert.equal(url.searchParams.get('artifact_id'), artifact.artifact_id)
  responseDigest = 'a'.repeat(64)
  await assert.rejects(adapter.readPdf(reference, artifact), /报告内容与所选版本不同/)
})

test('completion notification settings use the private revisioned API and never confirm an audit', async () => {
  const preference = {task_id: task.id, enabled: false, revision: 3, available: true, can_change: true, delivery: {state: 'not_due', reason: null}}
  const calls: {url: string; method: string; body: unknown}[] = []
  globalThis.fetch = async (input, options) => {calls.push({url: String(input), method: options?.method || 'GET', body: options?.body ? JSON.parse(String(options.body)) : null}); return json(preference)}
  const adapter = createNativeAuditAdapter({deepOffered: false, customers: []}).notifications!
  assert.deepEqual(await adapter.read(task.id), preference)
  assert.deepEqual(await adapter.save(task.id, false, 2), preference)
  assert.deepEqual(calls, [{url: '/audit-tasks/task-a/notification', method: 'GET', body: null}, {url: '/audit-tasks/task-a/notification', method: 'PUT', body: {enabled: false, expected_revision: 2}}])
})

test('native usage reads the enterprise projection with filters and never reports a charge', async () => {
  const calls: string[] = []
  const page = {organization_id: 'tenant-a', entries: []}
  globalThis.fetch = async (input, init) => {
    assert.equal(init?.method, 'GET')
    assert.equal(init?.credentials, 'include')
    calls.push(String(input)); return json(page)
  }
  assert.deepEqual(await nativeUsageAdapter.readUsage({range: '90d', search: '图纸 & Alice', page: 2, page_size: 10}), page)
  assert.equal(calls.length, 1)
  const url = new URL(calls[0], 'https://example.test')
  assert.equal(url.pathname, '/audit-usage')
  assert.equal(url.searchParams.get('range'), '90d')
  assert.equal(url.searchParams.get('search'), '图纸 & Alice')
  assert.equal(url.searchParams.get('page'), '2')
})

test('native archive preserves filters and the exact selected historical result', async () => {
  const calls: string[] = []
  globalThis.fetch = async input => {calls.push(String(input)); return json({})}
  await nativeArchiveAdapter.listDocuments({search: 'A & B', category_id: null, document_type: null, customer: '客户', initiator: null, from_date: null, through_date: null, page: 2, page_size: 10})
  await nativeArchiveAdapter.listHistory('document/a')
  await nativeArchiveAdapter.readHistory('document/a', {task_id: 'old-task', round_id: 'old-round', file_version_id: 'old-version', result_attempt: 2})
  const query = new URL(calls[0], 'https://example.test').searchParams
  assert.equal(query.get('search'), 'A & B')
  assert.equal(query.get('customer'), '客户')
  assert.equal(query.get('page'), '2')
  assert.equal(query.has('category_id'), false)
  assert.equal(calls[1], '/audit-archive/document%2Fa/history')
  assert.equal(calls[2], '/audit-archive/document%2Fa/history/result?task_id=old-task&round_id=old-round&file_version_id=old-version&result_attempt=2')
})

test('archive reads time out without replay and allow an explicit read of the same reference', async context => {
  context.mock.timers.enable({apis: ['setTimeout']})
  const calls: {url: string; method: string}[] = []
  const reference = {task_id: 'old-task', round_id: 'old-round', file_version_id: 'old-version', result_attempt: 2}
  const reads = [
    () => nativeArchiveAdapter.listDocuments({search: '', category_id: null, document_type: null, customer: null, initiator: null, from_date: null, through_date: null, page: 1, page_size: 10}),
    () => nativeArchiveAdapter.listHistory('document/a'),
    () => nativeArchiveAdapter.readHistory('document/a', reference),
  ]
  for (const phase of ['fetch', 'body']) for (const read of reads) {
    globalThis.fetch = async (input, init) => {
      calls.push({url: String(input), method: init?.method || 'GET'})
      const stalled = new Promise<never>((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {once: true}))
      return phase === 'fetch' ? stalled : {ok: true, json: () => stalled} as unknown as Response
    }
    const before = calls.length
    const pending = assert.rejects(read(), /读取审核文件超时，请重新读取/)
    await Promise.resolve()
    context.mock.timers.tick(45_000)
    await pending
    assert.equal(calls.length, before + 1)
  }
  assert.ok(calls.every(call => call.method === 'GET'))
  const frozenUrl = calls.at(-1)!.url
  globalThis.fetch = async input => {assert.equal(String(input), frozenUrl); return json({reference})}
  assert.deepEqual(await nativeArchiveAdapter.readHistory('document/a', reference), {reference})
})

test('frozen PDF and ZIP reads bound fetch and body stalls without automatic replay', async context => {
  context.mock.timers.enable({apis: ['setTimeout']})
  const reference = {task_id: 'old-task', round_id: 'old-round', file_version_id: 'old-version', result_attempt: 2, report_id: 'old-report'}
  const pdf = new TextEncoder().encode('%PDF-frozen-report'), zip = new TextEncoder().encode('PK-frozen-zip')
  const pdfArtifact = {artifact_id: 'frozen-pdf', filename: '原报告.pdf', content_type: 'application/pdf' as const, byte_size: pdf.length}
  const zipArtifact = {artifact_id: 'frozen-zip', filename: '原报告.zip', content_type: 'application/zip' as const, byte_size: zip.length}
  const job: components['schemas']['AuditReportPackage'] = {package_id: 'old-package', reports: [reference], receipt: {request_id: 'old-package-request', state: 'accepted', reason: null}, state: 'ready', artifact: zipArtifact, reason: null, retry_allowed: false}
  const reads = [
    {read: () => nativeReportAdapter.readPdf(reference, pdfArtifact), body: pdf, artifact: pdfArtifact},
    {read: () => nativeReportAdapter.readPackageFile!(job), body: zip, artifact: zipArtifact},
    {read: () => sharedReportAdapter('old-share').readPdf(reference, pdfArtifact), body: pdf, artifact: pdfArtifact},
    {read: () => readSharedArchive('old-share', zipArtifact), body: zip, artifact: zipArtifact},
  ]
  for (const phase of ['fetch', 'body']) for (const {read, body, artifact} of reads) {
    const calls: string[] = [], signals: AbortSignal[] = []
    globalThis.fetch = async (input, init) => {
      assert.equal(init?.method, 'GET'); assert.equal(init?.credentials, 'include')
      assert.ok(init?.signal); signals.push(init.signal); calls.push(String(input))
      // Deliberately ignore cancellation: local expiry must still settle the read.
      const stalled = new Promise<never>(() => {})
      return phase === 'fetch' ? stalled : {ok: true, blob: () => stalled} as unknown as Response
    }
    const pending = assert.rejects(read(), {name: 'TimeoutError'})
    await Promise.resolve()
    context.mock.timers.tick(45_000)
    await pending
    assert.equal(calls.length, 1); assert.equal(signals[0].aborted, true)
    const frozenUrl = calls[0]
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', body)), byte => byte.toString(16).padStart(2, '0')).join('')
    globalThis.fetch = async (input, init) => {
      assert.equal(String(input), frozenUrl); assert.equal(init?.method, 'GET'); assert.equal(init?.credentials, 'include')
      assert.ok(init?.signal); signals.push(init.signal); calls.push(String(input))
      return new Response(body, {headers: {'Content-Type': artifact.content_type, 'X-Content-SHA256': digest}})
    }
    assert.deepEqual(new Uint8Array(await (await read()).arrayBuffer()), body)
    context.mock.timers.tick(45_000)
    assert.equal(calls.length, 2); assert.equal(signals[1].aborted, false)
  }
})

test('a PDF read cannot become successful after its deadline even when fetch completes late', async context => {
  context.mock.timers.enable({apis: ['setTimeout']})
  const reference = {task_id: 'old-task', round_id: 'old-round', file_version_id: 'old-version', result_attempt: 2, report_id: 'old-report'}
  const artifact = {artifact_id: 'frozen-pdf', filename: '原报告.pdf', content_type: 'application/pdf' as const, byte_size: 8}
  let finish!: (response: Response) => void
  globalThis.fetch = async () => new Promise(resolve => {finish = resolve})
  const read = nativeReportAdapter.readPdf(reference, artifact)
  const expired = assert.rejects(read, {name: 'TimeoutError'})
  context.mock.timers.tick(45_000)
  await expired
  finish(new Response('%PDF-1.7', {headers: {'Content-Type': 'application/pdf'}}))
  await assert.rejects(read, {name: 'TimeoutError'})
})

test('Deep selection reaches task recognition and survives readback without starting an audit', async () => {
  const adapter = createNativeAuditAdapter({deepOffered: true, customers: []})
  const deepTask = {...task, analysis_mode: 'deep' as const}
  const calls: string[] = []
  globalThis.fetch = async (input, init) => {
    calls.push(`${init?.method || 'GET'} ${input}`)
    if (init?.method === 'POST') {
      assert.equal(String(input), '/audit-tasks')
      assert.equal(JSON.parse(String(init.body)).analysis_mode, 'deep')
    }
    return json(deepTask)
  }
  assert.equal(adapter.deepOffered, true)
  const matched = await adapter.match([file], 'deep', null, null, 'recognition-deep')
  assert.equal(matched.analysis_mode, 'deep')
  assert.equal((await adapter.readTask(matched.id)).analysis_mode, 'deep')
  assert.deepEqual(calls, ['POST /audit-tasks', 'GET /audit-tasks/task-a'])
})

test('native Drive selection pages through the real API and reads back a lost import reply', async () => {
  const adapter = createNativeAuditAdapter({deepOffered: false, customers: []})
  const id = '0a3b1077-f018-4cac-be45-16599020f20e'
  const source = {token: 'source-token', name: 'report.xlsx', type: 'file'}
  const ready = {...file.file, id, sha256: 'a'.repeat(64)}
  const calls: string[] = []
  globalThis.fetch = async (input, init) => {
    calls.push(`${init?.method} ${input}`)
    assert.equal(init?.credentials, 'include')
    if (String(input).startsWith('/integrations/feishu/files?')) return json({files: [source], next_page_token: null})
    if (init?.method === 'POST') {
      assert.deepEqual(JSON.parse(String(init.body)), {request_id: id, file_token: 'source-token', doc_type: 'file'})
      throw new TypeError('response lost after import')
    }
    return json({request_id: id, state: 'ready', file: ready, reason: null, retry_allowed: false})
  }
  assert.ok(adapter.drive)
  assert.deepEqual((await adapter.drive.list({pageToken: 'next / page', folderToken: 'chosen-folder'})).files, [source])
  const receipt = await adapter.upload({kind: 'feishu', file: source}, id)
  assert.equal(receipt.state, 'ready')
  assert.equal(receipt.file?.id, id)
  assert.deepEqual(calls, ['GET /integrations/feishu/files?folder_token=chosen-folder&page_token=next+%2F+page', 'POST /integrations/feishu/files/import', `GET /file-uploads/${id}`])
})

test('native Drive unknown results permit readback, never an automatic second import', async () => {
  const adapter = createNativeAuditAdapter({deepOffered: false, customers: []})
  const id = '0a3b1077-f018-4cac-be45-16599020f20e'
  let posts = 0, reads = 0
  globalThis.fetch = async (_input, init) => {
    if (init?.method === 'POST') {posts += 1; throw new TypeError('no response')}
    reads += 1
    return json({request_id: id, state: 'unknown', file: null, retry_allowed: false, reason: null})
  }
  assert.equal((await adapter.upload({kind: 'feishu', file: {token: 'token', name: 'cloud sheet', type: 'sheet'}}, id)).state, 'unknown')
  assert.equal((await adapter.readUpload(id)).state, 'unknown')
  assert.equal(posts, 1)
  assert.equal(reads, 2)
})

test('native Drive distinguishes a missing user grant from an unclassified provider failure', async () => {
  const adapter = createNativeAuditAdapter({deepOffered: false, customers: []})
  globalThis.fetch = async () => json({error: 'feishu_user_token_missing'}, 409)
  await assert.rejects(adapter.drive!.list(), {message: 'authorization_required'})
  const rejected = await adapter.upload({kind: 'feishu', file: {token: 'token', name: 'file.pdf', type: 'file'}}, 'request')
  assert.equal(rejected.state, 'failed')
  assert.equal(rejected.retry_allowed, false)
  globalThis.fetch = async () => json({error: 'feishu_folder_failed'}, 502)
  await assert.rejects(adapter.drive!.list(), error => error instanceof Error && error.message !== 'authorization_required')
})

test('native customer choices read the authenticated tenant rule directory', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async (input, init) => {
    assert.equal(String(input), '/audit-customers')
    assert.equal(init?.credentials, 'include')
    return json({customers: [{customer_id: 'customer-a', name: '客户 A'}]})
  }
  try {assert.deepEqual(await readAuditCustomers(), {customers: [{customer_id: 'customer-a', name: '客户 A'}]})}
  finally {globalThis.fetch = original}
})

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

test('native PDF regeneration reads the original request after a lost reply without repeating it', async () => {
  const reference = {task_id: 'task-a', round_id: 'round-a', file_version_id: 'version-a', result_attempt: 2, report_id: 'report-a'}
  const report = {state: 'pending' as const, report_id: 'report-a', reason: null}
  const update = {reference, receipt: {request_id: 'request-a', state: 'accepted' as const, reason: null}, report}
  const calls: string[] = []
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), 'http://example.test')
    calls.push(`${init?.method} ${url.pathname}`)
    assert.equal(init?.credentials, 'include')
    if (init?.method === 'POST') {assert.deepEqual(JSON.parse(String(init.body)), {request_id: 'request-a', reference}); throw new TypeError('reply lost')}
    assert.deepEqual(Object.fromEntries(url.searchParams), {round_id: 'round-a', file_version_id: 'version-a', result_attempt: '2'})
    return json(update)
  }
  await assert.rejects(nativeReportAdapter.regenerate!(reference, 'request-a'), /reply lost/)
  assert.deepEqual(await nativeReportAdapter.readRegeneration!(reference, 'request-a'), update)
  assert.deepEqual(calls, ['POST /audit-tasks/task-a/reports/report-a/regenerations', 'GET /audit-tasks/task-a/reports/report-a/regenerations/request-a'])
  globalThis.fetch = async () => json(null)
  const unknown = await nativeReportAdapter.readRegeneration!(reference, 'request-a')
  assert.equal(unknown.receipt.state, 'pending')
  assert.equal(unknown.receipt.request_id, 'request-a')
  assert.equal(unknown.report, null)
})

test('native PDF regeneration treats explicit policy refusals as rejected without submitting an audit', async () => {
  const reference = {task_id: 'task-a', round_id: 'round-a', file_version_id: 'version-a', result_attempt: 2, report_id: 'report-a'}
  for (const code of ['report_recovery_unavailable', 'audit_report_charging_unavailable', 'usage_quota_unavailable', 'usage_ai_disabled', 'usage_balance_empty']) {
    let calls = 0
    globalThis.fetch = async (input, init) => {
      calls += 1
      assert.equal(String(input), '/audit-tasks/task-a/reports/report-a/regenerations')
      assert.equal(init?.method, 'POST')
      return json({error: code}, 409)
    }
    const update = await nativeReportAdapter.regenerate!(reference, 'request-a')
    assert.equal(update.receipt.state, 'rejected')
    assert.equal(update.receipt.request_id, 'request-a')
    assert.equal(update.report, null)
    assert.equal(calls, 1)
  }
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

test('ZIP recovery binds the failed package attempt and reads its original request after a lost reply', async () => {
  const reports = ['a', 'b'].map(id => ({task_id: 'task-a', round_id: 'round-a', file_version_id: `version-${id}`, result_attempt: 1, report_id: `report-${id}`}))
  const failed: components['schemas']['AuditReportPackage'] = {package_id: 'package-a', reports, attempt: 1, receipt: {request_id: 'original-a', state: 'accepted', reason: null}, state: 'failed', artifact: null, reason: '打包失败', retry_allowed: true}
  const pending = {...failed, receipt: {...failed.receipt, request_id: 'recovery-a'}, state: 'pending' as const, retry_allowed: false}
  const calls: string[] = []
  globalThis.fetch = async (input, init) => {
    calls.push(`${init?.method} ${input}`)
    if (init?.method === 'POST') {
      assert.deepEqual(JSON.parse(String(init.body)), {request_id: 'recovery-a', package_id: 'package-a', expected_attempt: 1})
      throw new TypeError('reply lost')
    }
    return json(pending)
  }
  await assert.rejects(nativeReportAdapter.retryPackage!(failed, 'recovery-a'), /reply lost/)
  assert.deepEqual(await nativeReportAdapter.readPackage!('recovery-a'), pending)
  assert.deepEqual(calls, ['POST /audit-report-packages/original-a/retries', 'GET /audit-report-packages/recovery-a'])
})

test('server quota refusals remain explicit rejections for confirmation and ZIP recovery', async () => {
  const failed: components['schemas']['AuditReportPackage'] = {package_id: 'package-a', reports: [], attempt: 1, receipt: {request_id: 'original-a', state: 'accepted', reason: null}, state: 'failed', artifact: null, reason: '打包失败', retry_allowed: true}
  for (const code of ['usage_quota_unavailable', 'usage_ai_disabled', 'usage_balance_empty']) {
    const calls: string[] = []
    globalThis.fetch = async (input, init) => {
      calls.push(`${init?.method} ${input}`)
      return init?.method === 'POST' ? json({error: code}, 409) : json(task)
    }
    const confirmation = await createNativeAuditAdapter({deepOffered: false, customers: []}).confirm(task, 'accept-a')
    assert.equal(confirmation.acceptance?.state, 'rejected')
    assert.equal(confirmation.round_id, null)
    const recovery = await nativeReportAdapter.retryPackage!(failed, 'recovery-a')
    assert.equal(recovery.receipt.state, 'rejected')
    assert.equal(recovery.receipt.reason, confirmation.acceptance?.reason)
    assert.ok(recovery.receipt.reason)
    assert.deepEqual(calls, ['POST /audit-tasks/task-a/confirm', 'GET /audit-tasks/task-a?request_id=accept-a', 'POST /audit-report-packages/original-a/retries'])
  }
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

test('native file validation reads the original validation after a lost submit reply without resubmitting', async () => {
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

test('native file validation refuses a result for another validation request', async () => {
  globalThis.fetch = async () => json({id: 'another-request', source_file_id: 'file-a', source_file_version_id: 'version-a', status: 'completed', result: {state: 'valid', reasons: []}})
  const adapter = createNativeAuditAdapter({deepOffered: false, customers: []})
  await assert.rejects(adapter.readValidation({...file, validation_id: 'original-request'}), /文件检查结果不属于这份文件版本、用途或原请求/)
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
      assert.deepEqual(JSON.parse(String(init.body)), {request_id: 'accept-a', expected_revision: 2, expected_confirmation_basis_digest: task.confirmation_basis_digest})
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

test('customer conflicts are explicit no-write refusals', async () => {
  for (const [code, reason] of [
    ['customer_scope_conflict', '本次文件属于不同客户，审核没有开始。请按客户分开任务后再确认。'],
  ]) {
    const calls: string[] = []
    globalThis.fetch = async (url, init) => {
      calls.push(`${init?.method} ${url}`)
      return init?.method === 'POST' ? json({error: code}, 409) : json(task)
    }
    const current = await createNativeAuditAdapter({deepOffered: false, customers: []}).confirm(task, 'accept-a')
    assert.deepEqual(current.acceptance, {request_id: 'accept-a', state: 'rejected', reason})
    assert.equal(current.round_id, null)
    assert.deepEqual(calls, ['POST /audit-tasks/task-a/confirm', 'GET /audit-tasks/task-a?request_id=accept-a'])
  }
})

test('a superseded rule requires recognition again instead of silently accepting another rule', async () => {
  let posts = 0
  globalThis.fetch = async (_url, init) => {
    if (init?.method === 'POST') {posts += 1; return json({error: 'rule_candidate_expired'}, 409)}
    return json(task)
  }
  const current = await createNativeAuditAdapter({deepOffered: false, customers: []}).confirm(task, 'accept-stale')
  assert.equal(current.round_id, null)
  assert.deepEqual(current.acceptance, {request_id: 'accept-stale', state: 'rejected', reason: '匹配的规则已有新版本，审核没有开始。请返回上一步重新识别，再核对规则。'})
  assert.equal(posts, 1)
})

test('missing confirmation basis cannot be posted or manufactured by the native adapter', async () => {
  globalThis.fetch = async () => {assert.fail('no confirmation without server basis')}
  await assert.rejects(createNativeAuditAdapter({deepOffered: false, customers: []}).confirm({...task, confirmation_basis_digest: null}, 'accept-a'), /读取当前审核依据/)
})

test('changed server basis is a definite refusal and only the viewed digest is submitted', async () => {
  const calls: {method: string; body: unknown}[] = []
  const fresh = {...task, confirmation_basis_digest: 'b'.repeat(64)}
  globalThis.fetch = async (url, init) => {
    calls.push({method: init?.method || 'GET', body: init?.body ? JSON.parse(String(init.body)) : null})
    if (init?.method === 'POST') return json({error: 'task_confirmation_changed'}, 409)
    assert.equal(String(url), '/audit-tasks/task-a?request_id=accept-stale')
    return json({...fresh, acceptance: {request_id: 'accept-stale', state: 'rejected', reason: '审核依据已变化。'}})
  }
  const result = await createNativeAuditAdapter({deepOffered: false, customers: []}).confirm(task, 'accept-stale')
  assert.equal(result.acceptance!.state, 'rejected')
  assert.equal(result.confirmation_basis_digest, fresh.confirmation_basis_digest)
  assert.equal(result.round_id, null)
  assert.deepEqual(calls, [{method: 'POST', body: {request_id: 'accept-stale', expected_revision: 2, expected_confirmation_basis_digest: task.confirmation_basis_digest}}, {method: 'GET', body: null}])
})

test('authorization, server and unknown refusals do not grant another confirmation', async () => {
  for (const [status, code] of [[401, 'unauthenticated'], [403, 'access_denied'], [500, 'audit_task_charging_unavailable'], [409, 'unrecognized_conflict']] as const) {
    const calls: string[] = []
    globalThis.fetch = async (url, init) => {calls.push(`${init?.method} ${url}`); return json({error: code}, status)}
    await assert.rejects(createNativeAuditAdapter({deepOffered: false, customers: []}).confirm(task, 'accept-a'))
    assert.deepEqual(calls, ['POST /audit-tasks/task-a/confirm'])
  }
})

test('photo validation binds purpose to its own lost-reply identity and cannot reuse ordinary validation', async () => {
  const scope = {customer_preference: 'UAES', preset_key: 'U13'} as const
  const calls: {method: string; url: string}[] = []
  let photoId = ''
  globalThis.fetch = async (input, init) => {
    const url = String(input), method = init?.method || 'GET'
    calls.push({url, method})
    if (url.endsWith('/validation')) return json({validation: {id: 'ordinary', source_file_id: 'file-a', source_file_version_id: 'version-a', status: 'completed', result: {state: 'invalid', reasons: ['Office required']}}})
    if (method === 'POST') {
      const body = JSON.parse(String(init?.body))
      assert.deepEqual(body.scope, scope)
      photoId = body.request_id
      throw new TypeError('photo reply lost')
    }
    assert.equal(url, `/file-validations/${photoId}`)
    return json({id: photoId, scope, source_file_id: 'file-a', source_file_version_id: 'version-a', status: 'succeeded', result: {state: 'valid', reasons: []}})
  }
  const adapter = createNativeAuditAdapter({deepOffered: false, customers: ['UAES']})
  await assert.rejects(adapter.readValidation(file, scope), /lost/)
  const result = await adapter.readValidation(file, scope)
  assert.equal(result.validation_id, photoId)
  assert.deepEqual(result.validation_scope, scope)
  assert.equal(calls.filter(call => call.method === 'POST').length, 1)
})

test('a photo receipt cannot authorize ordinary validation or another original', async () => {
  const scope = {customer_preference: 'UAES', preset_key: 'U13'} as const
  globalThis.fetch = async () => json({id: 'original', scope, source_file_id: 'file-a', source_file_version_id: 'version-a', status: 'succeeded', result: {state: 'valid', reasons: []}})
  const adapter = createNativeAuditAdapter({deepOffered: false, customers: []})
  await assert.rejects(adapter.readValidation({...file, validation_id: 'original'}, null), /用途/)
  await assert.rejects(adapter.readValidation({...file, file: {...file.file, id: 'other'}, validation_id: 'original', validation_scope: scope}, scope), /文件检查结果不属于这份文件版本、用途或原请求/)
})

test('saved original validation identity restores its server-owned photo scope', async () => {
  const scope = {customer_preference: 'UAES', preset_key: 'U13'} as const
  globalThis.fetch = async () => json({id: 'saved', scope, source_file_id: 'file-a', source_file_version_id: 'version-a', status: 'succeeded', result: {state: 'valid', reasons: []}})
  const result = await createNativeAuditAdapter({deepOffered: false, customers: ['UAES']}).readValidation({...file, validation_id: 'saved'})
  assert.deepEqual(result.validation_scope, scope)
})

test('validation write loss keeps its original readback identity across adapter replacement', async () => {
  let id = ''
  globalThis.fetch = async (input, init) => {
    if (String(input).endsWith('/validation')) return json({validation: null})
    if (init?.method === 'POST') {id = JSON.parse(String(init.body)).request_id; throw new TypeError('lost')}
    assert.equal(String(input), `/file-validations/${id}`)
    return json({id, source_file_id: 'file-a', source_file_version_id: 'version-a', status: 'succeeded', result: {state: 'valid', reasons: []}})
  }
  let saved = ''
  try {await createNativeAuditAdapter({deepOffered: false, customers: []}).readValidation(file)}
  catch (error) {saved = (error as Error & {validationRequestId: string}).validationRequestId}
  assert.equal(saved, id)
  assert.equal((await createNativeAuditAdapter({deepOffered: false, customers: []}).readValidation({...file, validation_id: saved})).validation_id, id)
})


test('a Suite validation refusal reads the durable original receipt with its recovery guidance', async () => {
  const calls: string[] = []
  const acceptance = {request_id: 'accept-suite', state: 'rejected' as const, reason: '当前检查版本需要重新验证，本次未被接收。请在检查版本工作区重新验证并发布。'}
  globalThis.fetch = async (url, init) => {
    calls.push(`${init?.method} ${url}`)
    return init?.method === 'POST' ? json({error: 'suite_validation_required'}, 409) : json({...task, acceptance})
  }
  const adapter = createNativeAuditAdapter({deepOffered: false, customers: []})
  const recovered = await adapter.confirm(task, 'accept-suite')
  assert.deepEqual(recovered.acceptance, acceptance)
  assert.deepEqual(calls, ['POST /audit-tasks/task-a/confirm', 'GET /audit-tasks/task-a?request_id=accept-suite'])
})

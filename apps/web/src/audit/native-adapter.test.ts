import assert from 'node:assert/strict'
import test, {afterEach} from 'node:test'
import type {components} from '../generated/openapi.ts'
import {createNativeAuditAdapter, nativeArchiveAdapter, nativeDisplayAdapter, nativeFeedbackAdapter, nativeReportAdapter, nativeSharingAdapter, nativeSupportAdapter, nativeTemplateAdapter, nativeUsageAdapter, sharedReportAdapter} from './native-adapter.ts'
import {readReportPdf, readReportZip} from './report-contract.ts'
import {requestTrace} from '../api.ts'
import {readTemplateZip} from './template-contract.ts'
import {zipSync} from 'fflate'

type Task = components['schemas']['AuditTask']
const fetchBefore = globalThis.fetch
Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})
const file: Task['files'][number] = {file: {id: 'version-a', file_id: 'file-a', original_filename: 'a.xlsx', content_type: 'application/octet-stream', byte_size: 3, status: 'ready', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: 'pending', reasons: []}, match: null}
const task: Task = {id: 'task-a', number: 'PPAP-20261016-001', state: 'pending_confirm', analysis_mode: 'standard', created_at: '2026-10-16T00:00:00Z', updated_at: '2026-10-16T00:00:00Z', source_draft_id: 'task-a', round_id: null, customer_preference: null, completion_reason: null, estimated_points: 0, files: [file], input_revision: 2}
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

test('native feedback submits frozen versions and reads the same attempt without a second submission', async () => {
  const request = {request_id: 'original-request', file_version_ids: ['v1', 'v2']}
  const receipt = {request, attempt_id: 'original-attempt', state: 'unknown', submission_id: null, received_file_version_ids: [], received_at: null, retry_allowed: false, reason: null}
  const calls: string[] = []
  globalThis.fetch = async (input, init) => {
    assert.equal(init?.credentials, 'include')
    calls.push(`${init?.method} ${input}`)
    if (init?.method === 'POST') assert.deepEqual(JSON.parse(String(init.body)), request)
    return json(receipt)
  }
  assert.deepEqual(await nativeFeedbackAdapter.submit!(request, 'original-attempt'), receipt)
  assert.deepEqual(await nativeFeedbackAdapter.read!(request, 'original-attempt'), receipt)
  assert.deepEqual(calls, ['POST /audit-template-feedback/original-request/attempts/original-attempt', 'GET /audit-template-feedback/original-request/attempts/original-attempt'])
})

test('native feedback history is an authenticated read and never submits or uploads', async () => {
  const calls: string[] = []
  globalThis.fetch = async (input, options) => {
    assert.equal(options?.method, 'GET')
    assert.equal(options?.credentials, 'include')
    assert.equal(options?.body, undefined)
    calls.push(String(input))
    return json({page: 2, page_size: 10, total: 1, entries: []})
  }
  assert.deepEqual(await nativeFeedbackAdapter.listHistory!(2), {page: 2, page_size: 10, total: 1, entries: []})
  assert.deepEqual(calls, ['/audit-template-feedback?page=2'])
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
  assert.deepEqual((await adapter.drive.list('next / page')).files, [source])
  const receipt = await adapter.upload({kind: 'feishu', file: source}, id)
  assert.equal(receipt.state, 'ready')
  assert.equal(receipt.file?.id, id)
  assert.deepEqual(calls, ['GET /integrations/feishu/files?page_token=next+%2F+page', 'POST /integrations/feishu/files/import', `GET /file-uploads/${id}`])
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

test('native template coverage reads the authenticated catalog without inventing template files', async () => {
  const result = {coverage_universe: ['控制计划'], customers: [{customer_id: 'customer a', name: 'Customer A', effective_rule_types: ['控制计划']}]}
  globalThis.fetch = async (input, init) => {
    assert.equal(String(input), '/audit-template-catalog')
    assert.equal(init?.method, 'GET')
    assert.equal(init?.credentials, 'include')
    assert.equal(init?.body, undefined)
    return json(result)
  }
  assert.deepEqual(await nativeTemplateAdapter.readCatalog(), result)
})

test('native templates read the requested customer and frozen version with a content digest', async () => {
  const bytes = new TextEncoder().encode('real-template')
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('')
  const selected = {reference: {customer_id: '客户 A', template_id: 'template/a', file_version_id: 'frozen/a'}, file: {...file.file, id: 'frozen/a', file_id: 'template/a', byte_size: bytes.length, sha256: digest}, document_type: 'Excel', updated_at: '2026-10-18T00:00:00Z', preview_access: {state: 'allowed' as const, reason: null}, download_access: {state: 'allowed' as const, reason: null}, share_access: {state: 'unknown' as const, reason: null}}
  const query = {customer_id: '客户 A', search: 'A & B', page: 2, page_size: 10 as const}
  const calls: string[] = []
  globalThis.fetch = async (input, init) => {
    assert.equal(init?.credentials, 'include')
    calls.push(String(input))
    return String(input).includes('/versions/') ? new Response(bytes, {headers: {'Content-Type': 'application/octet-stream', 'X-Content-SHA256': digest}}) : json({query, items: []})
  }
  await nativeTemplateAdapter.listTemplates!(query)
  const result = await nativeTemplateAdapter.readFile!(selected, 'preview')
  assert.deepEqual(result.reference, selected.reference)
  assert.equal(await result.blob.text(), 'real-template')
  assert.equal(new URL(calls[0], 'https://example.test').searchParams.get('search'), 'A & B')
  assert.equal(calls[1], '/audit-templates/template%2Fa/versions/frozen%2Fa?customer_id=%E5%AE%A2%E6%88%B7+A&purpose=preview')
  globalThis.fetch = async () => new Response('wrong-content', {headers: {'Content-Type': 'application/octet-stream', 'X-Content-SHA256': digest}})
  await assert.rejects(nativeTemplateAdapter.readFile!(selected, 'download'))
})

test('native template ZIP reads the complete frozen folder and refuses a changed scope before downloading', async () => {
  const folder = {customer_id: '客户 A', folder_id: 'folder-a', folder_revision: 'revision-a', folder_template_count: 2}
  const entries = ['one.xlsx', 'two.xlsx'].map((filename, index) => ({reference: {customer_id: folder.customer_id, template_id: `template-${index}`, file_version_id: `version-${index}`}, filename, byte_size: 3}))
  const bytes = new Uint8Array(zipSync({'one.xlsx': new TextEncoder().encode('one'), 'two.xlsx': new TextEncoder().encode('two')}))
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('')
  const snapshot = {customer_id: folder.customer_id, folder_id: folder.folder_id, folder_revision: folder.folder_revision, templates: entries, manifest_filename: null, filename: '客户模板.zip', byte_size: bytes.length, complete: true, access: {state: 'allowed', reason: null}}
  const calls: string[] = []
  globalThis.fetch = async (input, init) => {
    assert.equal(init?.credentials, 'include')
    calls.push(String(input))
    return String(input).includes('/download?') ? new Response(bytes, {headers: {'Content-Type': 'application/zip', 'X-Content-SHA256': digest}}) : json(snapshot)
  }
  assert.deepEqual((await readTemplateZip(nativeTemplateAdapter, folder)).snapshot.templates, entries)
  assert.equal(calls.length, 2)
  assert.equal(new URL(calls[1], 'https://example.test').searchParams.get('folder_template_count'), '2')
  calls.length = 0
  snapshot.folder_revision = 'another-revision'
  await assert.rejects(readTemplateZip(nativeTemplateAdapter, folder))
  assert.equal(calls.length, 1)
})

test('native feedback keeps a local attachment request and does not claim an operator submission', async () => {
  const adapter = nativeFeedbackAdapter
  const id = 'ddb1466b-4a8d-422e-859f-25bf756697ae'
  const source = new File(['abc'], 'template.zip', {type: 'application/zip'})
  const version = {id, file_id: 'feedback-file', original_filename: source.name, content_type: source.type, byte_size: 3, status: 'staging', sha256: null}
  const ready = {...version, status: 'ready', sha256: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'}
  const calls: string[] = []
  globalThis.fetch = async (input, init) => {
    const path = new URL(String(input), 'http://example.test').pathname
    calls.push(`${init?.method} ${path}`)
    assert.equal(init?.credentials, 'include')
    if (init?.method === 'POST') {assert.equal(JSON.parse(String(init.body)).request_id, id); return json(version)}
    if (init?.method === 'PUT') {assert.equal(init.body, source); return json(ready)}
    if (path.endsWith('/download')) return new Response('abc', {headers: {'Content-Type': 'application/zip', 'X-Content-SHA256': ready.sha256}})
    if (path.startsWith('/files/')) return json(ready)
    return json({request_id: id, state: 'ready', file: ready, reason: null, retry_allowed: false})
  }
  assert.equal((await adapter.upload({kind: 'local', file: source}, id)).state, 'ready')
  const received = (await adapter.readUpload(id)).file!
  assert.equal(received.id, id)
  const attachment = await adapter.readAttachment(received)
  assert.equal(attachment.file_version_id, id)
  assert.equal(await attachment.blob.text(), 'abc')
  assert.equal(typeof adapter.submit, 'function')
  assert.equal(typeof adapter.read, 'function')
  assert.deepEqual(calls, ['POST /files', `PUT /files/feedback-file/versions/${id}/blob`, `GET /file-uploads/${id}`, `GET /files/feedback-file/versions/${id}`, `GET /files/feedback-file/versions/${id}/download`])
})

test('native feedback refuses invalid local attachments before any possible server effect', async () => {
  class Oversize extends File {override get size() {return 200 * 1024 * 1024 + 1}}
  globalThis.fetch = async () => {throw new Error('must not send')}
  for (const source of [new File(['exe'], 'run.exe'), new File([], 'empty.zip'), new Oversize(['one'], 'huge.zip')]) {
    const receipt = await nativeFeedbackAdapter.upload({kind: 'local', file: source}, 'local-request')
    assert.equal(receipt.state, 'failed')
    assert.equal(receipt.request_id, 'local-request')
    assert.equal(receipt.retry_allowed, false)
    assert.equal(receipt.file, null)
    assert.ok(receipt.reason)
  }
})

test('native feedback resumes a lost create response with the original request, version and bytes', async () => {
  const id = '5f5fe1ad-1ad2-44b9-bae7-5effbcaaea35', source = new File(['abc'], 'photo.png', {type: 'image/png'})
  const version = {id, file_id: 'feedback-file', original_filename: source.name, content_type: source.type, byte_size: 3, status: 'staging', sha256: null}
  let creates = 0
  const calls: string[] = []
  globalThis.fetch = async (input, init) => {
    calls.push(`${init?.method} ${new URL(String(input), 'http://example.test').pathname}`)
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      assert.equal(body.request_id, id)
      assert.equal(body.sha256, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
      if (++creates === 1) throw new TypeError('create reply lost after registration')
      return json(version)
    }
    if (init?.method === 'GET') return json({request_id: id, state: 'uploading', file: version, resume_allowed: true, retry_allowed: false, reason: null})
    assert.equal(init?.body, source)
    return json({...version, status: 'ready', sha256: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'})
  }
  const staged = await nativeFeedbackAdapter.upload({kind: 'local', file: source}, id)
  assert.equal(staged.state, 'uploading')
  assert.equal(staged.resume_allowed, true)
  const resumed = await nativeFeedbackAdapter.upload({kind: 'local', file: source}, staged.request_id)
  assert.equal(resumed.state, 'ready')
  assert.equal(resumed.file?.id, staged.file?.id)
  assert.deepEqual(calls, ['POST /files', `GET /file-uploads/${id}`, 'POST /files', `PUT /files/feedback-file/versions/${id}/blob`])
})

test('native feedback rejects mismatched server identities and bytes without relabelling another attachment', async () => {
  const file: components['schemas']['FileVersion'] = {id: 'version-feedback', file_id: 'file-feedback', original_filename: 'template.zip', content_type: 'application/zip', byte_size: 3, status: 'ready', sha256: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'}
  for (const change of [{id: 'other-version'}, {file_id: 'other-file'}, {status: 'staging'}, {original_filename: 'other.zip'}, {content_type: 'text/html'}, {byte_size: 4}, {sha256: '0'.repeat(64)}]) {
    let reads = 0
    globalThis.fetch = async () => {reads += 1; return json({...file, ...change})}
    await assert.rejects(nativeFeedbackAdapter.readAttachment(file))
    assert.equal(reads, 1, 'wrong metadata must not start a byte download')
  }
  for (const response of [new Response('abd', {headers: {'Content-Type': file.content_type, 'X-Content-SHA256': file.sha256!}}), new Response('abc', {headers: {'Content-Type': 'text/html', 'X-Content-SHA256': file.sha256!}}), new Response('ab', {headers: {'Content-Type': file.content_type, 'X-Content-SHA256': file.sha256!}}), new Response('abc', {headers: {'Content-Type': file.content_type, 'X-Content-SHA256': '0'.repeat(64)}})]) {
    globalThis.fetch = async input => String(input).endsWith('/download') ? response : json(file)
    await assert.rejects(nativeFeedbackAdapter.readAttachment(file))
  }
  globalThis.fetch = async input => String(input).endsWith('/download') ? new Response('denied', {status: 403, headers: {'X-Request-ID': 'feedback-download-denied'}}) : json(file)
  await assert.rejects(nativeFeedbackAdapter.readAttachment(file), cause => requestTrace(cause) === 'feedback-download-denied')
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
  for (const code of ['report_recovery_unavailable', 'audit_report_charging_unavailable']) {
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

test('authorization, server and unknown refusals do not grant another confirmation', async () => {
  for (const [status, code] of [[401, 'unauthenticated'], [403, 'access_denied'], [500, 'audit_task_charging_unavailable'], [409, 'unrecognized_conflict']] as const) {
    const calls: string[] = []
    globalThis.fetch = async (url, init) => {calls.push(`${init?.method} ${url}`); return json({error: code}, status)}
    await assert.rejects(createNativeAuditAdapter({deepOffered: false, customers: []}).confirm(task, 'accept-a'))
    assert.deepEqual(calls, ['POST /audit-tasks/task-a/confirm'])
  }
})

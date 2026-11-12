import assert from 'node:assert/strict'
import test, {afterEach} from 'node:test'
import {createNativeTemplateLibraryAdapter} from './template-library-adapter.ts'

const fetchBefore = globalThis.fetch
Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})
afterEach(() => {globalThis.fetch = fetchBefore})
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}})

test('a retried submission reuses the received versions under the same request id', async () => {
  const staged = new Map<string, {request_id: string; sha256: string; original_filename: string; byte_size: number; content_type: string}>()
  const calls: string[] = [], submissions: unknown[] = []
  let submitReplies = 0
  globalThis.fetch = async (address, init) => {
    assert.equal(init?.credentials, 'include')
    const path = new URL(String(address), 'https://example.test').pathname, method = init?.method || 'GET'
    calls.push(`${method} ${path}`)
    if (method === 'POST' && path === '/files') {
      const body = JSON.parse(String(init!.body))
      staged.set(body.request_id, body)
      return json({id: body.request_id, file_id: 'file-' + body.original_filename, original_filename: body.original_filename, content_type: body.content_type, byte_size: body.byte_size, status: 'staging', sha256: null}, 201)
    }
    if (method === 'PUT') {
      const version = path.split('/')[4], body = staged.get(version)!
      return json({id: version, file_id: 'file-' + body.original_filename, original_filename: body.original_filename, content_type: body.content_type, byte_size: body.byte_size, status: 'ready', sha256: body.sha256})
    }
    assert.equal(`${method} ${path}`, 'POST /template-submissions')
    submissions.push(JSON.parse(String(init!.body)))
    if (!submitReplies++) return json({error: 'temporarily_unavailable'}, 503)
    return json({request_id: 'submission-one', state: 'received', file_count: 2, created_at: '2026-11-11T00:00:00Z'})
  }
  const adapter = createNativeTemplateLibraryAdapter()
  assert.equal(adapter.feishu, undefined)
  const files = [new File(['a'], '模板一.xlsx', {type: 'application/vnd.ms-excel'}), new File(['bb'], '模板包.zip', {type: 'application/zip'})]
  await assert.rejects(adapter.submit({requestId: 'submission-one', customer: null, files}))
  const receipt = await adapter.submit({requestId: 'submission-one', customer: null, files})
  assert.equal(receipt.state, 'received')
  assert.equal(calls.filter(call => call === 'POST /files').length, 2)
  assert.equal(submissions.length, 2)
  assert.deepEqual(submissions[0], submissions[1])
  assert.deepEqual((submissions[1] as {files: {file_id: string}[]}).files.map(file => file.file_id), ['file-模板一.xlsx', 'file-模板包.zip'])
})

test('saving to Feishu exists only with personal drive upload and posts the chosen folder', async () => {
  const bodies: unknown[] = []
  globalThis.fetch = async (address, init) => {
    assert.equal(new URL(String(address), 'https://example.test').pathname, '/template-library/uaes/feishu')
    bodies.push(JSON.parse(String(init!.body)))
    return json({customer_id: 'uaes', folder_token: 'folder-one', results: []})
  }
  const adapter = createNativeTemplateLibraryAdapter({personalDriveUpload: true})
  await adapter.feishu!.save('uaes', 'folder-one')
  await adapter.feishu!.save('uaes', 'folder-one', ['uaes-psw'])
  assert.deepEqual(bodies, [{folder_token: 'folder-one'}, {folder_token: 'folder-one', template_ids: ['uaes-psw']}])
})

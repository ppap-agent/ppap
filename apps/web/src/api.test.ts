import assert from 'node:assert/strict'
import test, {afterEach} from 'node:test'
import {apiUrl, connect, readLocalUpload, request, requestTrace, upload, uploadLocalVersion, UploadResultUnknown, type Material} from './api.ts'

const originalFetch = globalThis.fetch
Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}})
}

function installFetch(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const calls: Array<{url: string; init?: RequestInit}> = []
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    calls.push({url, init})
    return handler(url, init)
  }
  return calls
}

afterEach(() => {
  globalThis.fetch = originalFetch
})

test('formalization conflicts explain the next step instead of a raw error code', async () => {
  const messages = new Set<string>()
  for (const [code, status] of [['operation_conflict', 409], ['source_changed', 409], ['conflicting_confirmation', 409], ['not_confirmable', 409], ['source_unchecked', 409], ['model_unavailable', 503], ['model_consumption_refused', 422]] as const) {
    installFetch(() => jsonResponse({error: code}, status))
    await assert.rejects(request('/rules/rule-1/formalizations/c1/generations', 'POST', {}), error => {
      assert.ok(error instanceof Error)
      assert.equal(error.message.includes(code), false)
      messages.add(error.message)
      return true
    })
  }
  assert.equal(messages.size, 7)
})

test('rule source rejection explains the required correction without claiming publication', async () => {
  installFetch(() => jsonResponse({error: 'rule_source_unverified'}, 400))
  await assert.rejects(request('/rules', 'POST', {}), /请重新选择原文位置并核对，草稿未发布/)
})

test('connect reads runtime config then public-config', async () => {
  const calls = installFetch(url => {
    if (url === '/runtime-config.json') return jsonResponse({api_url: 'http://api.example'})
    if (url === 'http://api.example/public-config') {
      return jsonResponse({api_url: 'http://api.example', profile: 'isolated-dev', feishu_app_id: 'cli_app'})
    }
    throw new Error('unexpected ' + url)
  })
  await connect()
  assert.deepEqual(calls.map(call => call.url), ['/runtime-config.json', 'http://api.example/public-config'])
  assert.equal(apiUrl, 'http://api.example')
})

test('product requests send cookies and no custom token header', async () => {
  installFetch(url => {
    if (url === '/runtime-config.json') return jsonResponse({api_url: 'http://api.example'})
    if (url === 'http://api.example/public-config') {
      return jsonResponse({api_url: 'http://api.example', profile: 'isolated-dev'})
    }
    throw new Error('unexpected ' + url)
  })
  await connect()
  const calls = installFetch((url, init) => {
    assert.equal(url, 'http://api.example/me')
    assert.equal(init?.credentials, 'include')
    const headers = new Headers(init?.headers)
    assert.equal(headers.get('content-type'), 'application/json')
    assert.equal(headers.get('x-token'), null)
    return jsonResponse({display_name: 'alice'})
  })
  await request('/me')
  assert.equal(calls.length, 1)
  assert.equal(calls[0]?.init?.method, 'GET')
})

test('a failed product request preserves the server request identifier separately from its business request', async () => {
  installFetch(() => new Response(JSON.stringify({error: 'access_denied'}), {status: 403, headers: {'Content-Type': 'application/json', 'X-Request-ID': 'server-request.2026-170'}}))
  await assert.rejects(request('/audit-tasks/task/confirm', 'POST', {request_id: 'business-request'}), error => {
    assert.ok(error instanceof Error)
    assert.equal(error.message, '没有访问这份资料的权限。')
    assert.equal((error as Error & {traceId?: string}).traceId, 'server-request.2026-170')
    assert.equal('requestId' in error, false)
    return true
  })
})

test('an unreadable successful response retains its request identifier without pretending the operation failed', async () => {
  installFetch(() => new Response('not-json', {status: 200, headers: {'X-Request-ID': 'server-unreadable-170'}}))
  await assert.rejects(request('/audit-tasks/task/confirm', 'POST', {request_id: 'business-request'}), error => {
    assert.ok(error instanceof TypeError)
    assert.equal((error as Error & {traceId?: string}).traceId, 'server-unreadable-170')
    assert.equal('status' in error, false)
    return true
  })
})

test('a failure without a JSON error object still carries the response trace', async () => {
  installFetch(() => new Response('null', {status: 503, headers: {'Content-Type': 'application/json', 'X-Request-ID': 'server-no-error-object'}}))
  await assert.rejects(request('/audit-tasks/task'), error => requestTrace(error) === 'server-no-error-object')
})

test('non-JSON failures retain only a bounded server identifier and later requests do not inherit it', async () => {
  installFetch(() => new Response('gateway error', {status: 503, headers: {'X-Request-ID': 'server-gateway-170'}}))
  await assert.rejects(request('/audit-tasks/task'), error => requestTrace(error) === 'server-gateway-170')
  for (const traceId of ['', 'short', 'contains secret/value', 'x'.repeat(129)]) {
    installFetch(() => new Response('gateway error', {status: 503, headers: traceId ? {'X-Request-ID': traceId} : {}}))
    await assert.rejects(request('/audit-tasks/task'), error => {
      assert.ok(error instanceof Error)
      assert.equal('traceId' in error, false)
      return true
    })
  }
  installFetch(() => {throw new TypeError('network unavailable')})
  await assert.rejects(request('/audit-tasks/task'), error => requestTrace(error) === undefined)
})

test('offline requests have no service trace and do not send a request', async () => {
  const calls = installFetch(() => {throw new Error('must not send')})
  Object.defineProperty(globalThis, 'navigator', {value: {onLine: false}, configurable: true})
  try {
    await assert.rejects(request('/audit-tasks/task'), error => error instanceof Error && error.message.includes('离线') && requestTrace(error) === undefined)
    assert.deepEqual(calls, [])
  } finally {Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})}
})

test('local upload fixes the request and file hash before sending bytes', async () => {
  const id = '04b7d172-a5b6-44c6-98e1-91cd012fc5d8'
  const sha256 = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
  const file = new File(['abc'], 'control.xlsx', {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
  const version = {id, file_id: 'file', original_filename: file.name, byte_size: 3, content_type: file.type, status: 'staging', sha256: null}
  const staged: string[] = []
  const calls = installFetch((url, init) => {
    if (url.endsWith('/files')) {
      assert.deepEqual(JSON.parse(String(init?.body)), {request_id: id, sha256, original_filename: file.name, byte_size: 3, content_type: file.type})
      return jsonResponse(version)
    }
    assert.equal(url, `${apiUrl}/files/file/versions/${id}/blob`)
    assert.equal(init?.method, 'PUT')
    assert.equal(init?.body, file)
    assert.equal(init?.credentials, 'include')
    return jsonResponse({...version, status: 'ready', sha256})
  })
  const receipt = await uploadLocalVersion(file, id, next => staged.push(next.id))
  assert.deepEqual(staged, [id])
  assert.equal(receipt.state, 'ready')
  assert.equal(receipt.file?.id, id)
  assert.equal(receipt.file?.sha256, sha256)
  assert.deepEqual(calls.map(call => call.init?.method), ['POST', 'PUT'])
})

test('local upload accepts the server canonical filename without changing its fixed bytes', async () => {
  const id = 'd8d3bf35-2145-4094-97ea-a5f149b5891b'
  const sha256 = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
  const file = new File(['abc'], ' report.xlsx')
  const version = {id, file_id: 'file', original_filename: 'report.xlsx', byte_size: 3, content_type: 'application/octet-stream', status: 'staging', sha256: null}
  const staged: string[] = []
  const calls = installFetch((url, init) => {
    if (init?.method === 'POST') {
      assert.equal(JSON.parse(String(init.body)).original_filename, ' report.xlsx')
      return jsonResponse(version)
    }
    if (init?.method === 'GET') return jsonResponse({request_id: id, state: 'uploading', file: version, reason: null, retry_allowed: false, resume_allowed: true})
    assert.equal(url, `${apiUrl}/files/file/versions/${id}/blob`)
    assert.equal(init?.method, 'PUT')
    assert.equal(init?.body, file)
    return jsonResponse({...version, status: 'ready', sha256})
  })
  const receipt = await uploadLocalVersion(file, id, next => staged.push(next.original_filename))
  assert.equal(receipt.state, 'ready')
  assert.equal(receipt.file?.original_filename, 'report.xlsx')
  assert.equal(receipt.file?.id, id)
  assert.equal(receipt.file?.sha256, sha256)
  assert.deepEqual(staged, ['report.xlsx'])
  assert.deepEqual(calls.map(call => call.init?.method), ['POST', 'PUT'])
})

test('a lost create reply reads the same upload without sending bytes or another create', async () => {
  const id = '1702b78f-bf60-453a-a68e-f54eaa1b991b'
  const file = new File(['abc'], 'control.xlsx')
  const version = {id, file_id: 'file', original_filename: file.name, byte_size: 3, content_type: 'application/octet-stream', status: 'staging', sha256: null}
  const staged: string[] = []
  const calls = installFetch((url, init) => {
    if (init?.method === 'POST') throw new TypeError('reply lost')
    assert.equal(url, `${apiUrl}/file-uploads/${id}`)
    assert.equal(init?.method, 'GET')
    return jsonResponse({request_id: id, state: 'uploading', file: version, reason: '尚未收到原件', retry_allowed: false, resume_allowed: true})
  })
  const receipt = await uploadLocalVersion(file, id, next => staged.push(next.id))
  assert.equal(receipt.state, 'uploading')
  assert.equal(receipt.request_id, id)
  assert.deepEqual(staged, [id])
  assert.deepEqual(calls.map(call => call.init?.method), ['POST', 'GET'])
})

test('legacy upload preserves its staged material and reads a lost blob reply by the same request', async () => {
  const file = new File(['abc'], 'control.xlsx')
  const sha256 = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
  let id = ''
  const staged: Material[] = []
  const version = () => ({id, file_id: 'file', original_filename: file.name, byte_size: 3, content_type: 'application/octet-stream', status: 'staging', sha256: null})
  const calls = installFetch((url, init) => {
    if (init?.method === 'POST') {
      const input = JSON.parse(String(init.body))
      assert.match(input.request_id, /^[a-f0-9-]{36}$/)
      assert.equal(input.sha256, sha256)
      id = input.request_id
      return jsonResponse(version())
    }
    if (init?.method === 'PUT') throw new TypeError('blob reply lost')
    assert.equal(url, `${apiUrl}/file-uploads/${id}`)
    return jsonResponse({request_id: id, state: 'ready', file: {...version(), status: 'ready', sha256}, reason: null, retry_allowed: false})
  })
  const material = await upload(file, 'target', next => staged.push(next))
  assert.deepEqual(material, {file_id: 'file', file_version_id: id, original_filename: file.name, role: 'target', rule_version_id: null})
  assert.deepEqual(staged, [material])
  assert.deepEqual(calls.map(call => call.init?.method), ['POST', 'PUT', 'GET'])
})

test('changed input under the same upload request is a conflict, not permission to resume or retry', async () => {
  const id = 'bb8ebc86-08ce-4bdd-8b29-d85e93087124'
  const calls = installFetch(() => jsonResponse({error: 'upload_request_conflict'}, 409))
  const receipt = await uploadLocalVersion(new File(['changed'], 'control.xlsx'), id)
  assert.equal(receipt.state, 'failed')
  assert.equal(receipt.retry_allowed, false)
  assert.equal(receipt.request_id, id)
  assert.deepEqual(calls.map(call => call.init?.method), ['POST'])
})

test('a lost blob reply and failed readback retain the known staged material', async () => {
  const file = new File(['abc'], 'control.xlsx')
  let id = ''
  let staged: Material | undefined
  const calls = installFetch((_url, init) => {
    if (init?.method === 'POST') {
      id = JSON.parse(String(init.body)).request_id
      return jsonResponse({id, file_id: 'file', original_filename: file.name, byte_size: 3, content_type: 'application/octet-stream', status: 'staging', sha256: null})
    }
    throw new TypeError('connection lost')
  })
  await assert.rejects(upload(file, 'reference', next => {staged = next}), error => {
    assert.ok(error instanceof UploadResultUnknown)
    assert.deepEqual(error.material, staged)
    assert.equal(error.requestId, id)
    return true
  })
  assert.deepEqual(calls.map(call => call.init?.method), ['POST', 'PUT', 'GET'])
})

test('reading an upload refuses another request or a ready receipt without a ready version', async () => {
  const id = '5b00e316-5374-44ea-8f6b-72b8bfe91b35'
  installFetch(() => jsonResponse({request_id: 'other', state: 'unknown', file: null, retry_allowed: false, reason: null}))
  await assert.rejects(readLocalUpload(id), /原请求不一致/)
  installFetch(() => jsonResponse({request_id: id, state: 'ready', file: null, retry_allowed: false, reason: null}))
  await assert.rejects(readLocalUpload(id), /原请求不一致/)
})

test('an upload above 200 MiB is rejected before any server request', async () => {
  class OversizeFile extends File {override get size() {return 200 * 1024 * 1024 + 1}}
  const calls = installFetch(() => {throw new Error('must not send')})
  await assert.rejects(uploadLocalVersion(new OversizeFile(['x'], 'large.xlsx'), '45f7ae3d-a650-413f-ae52-78580816ea9e'), /超过 200 MB/)
  assert.deepEqual(calls, [])
})

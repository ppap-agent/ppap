import assert from 'node:assert/strict'
import test, {afterEach} from 'node:test'
import {apiUrl, connect, deepOffered, feishuAppId, profile, readLocalUpload, request, upload, uploadLocalVersion, UploadResultUnknown, type Material} from './api.ts'

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

test('connect reads runtime config then public-config and gates deep mode', async () => {
  const calls = installFetch(url => {
    if (url === '/runtime-config.json') return jsonResponse({api_url: 'http://api.example'})
    if (url === 'http://api.example/public-config') {
      return jsonResponse({api_url: 'http://api.example', profile: 'isolated-dev', deep_offered: true, feishu_app_id: 'cli_app'})
    }
    throw new Error('unexpected ' + url)
  })
  await connect()
  assert.deepEqual(calls.map(call => call.url), ['/runtime-config.json', 'http://api.example/public-config'])
  assert.equal(apiUrl, 'http://api.example')
  assert.equal(profile, 'isolated-dev')
  assert.equal(deepOffered, true)
  assert.equal(feishuAppId, 'cli_app')
})

test('connect leaves deep mode off unless public-config offers it', async () => {
  installFetch(url => {
    if (url === '/runtime-config.json') return jsonResponse({api_url: 'http://api.example'})
    if (url === 'http://api.example/public-config') {
      return jsonResponse({api_url: 'http://api.example', profile: 'production'})
    }
    throw new Error('unexpected ' + url)
  })
  await connect()
  assert.equal(deepOffered, false)
  assert.equal(profile, 'production')
})

test('product requests send cookies and no custom token header', async () => {
  installFetch(url => {
    if (url === '/runtime-config.json') return jsonResponse({api_url: 'http://api.example'})
    if (url === 'http://api.example/public-config') {
      return jsonResponse({api_url: 'http://api.example', profile: 'isolated-dev', deep_offered: false})
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
  const me = await request<{display_name: string}>('/me')
  assert.equal(me.display_name, 'alice')
  assert.equal(calls.length, 1)
  assert.equal(calls[0]?.init?.method, 'GET')
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

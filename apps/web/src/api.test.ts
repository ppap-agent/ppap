import assert from 'node:assert/strict'
import test, {afterEach} from 'node:test'
import {apiUrl, connect, deepOffered, feishuAppId, profile, request} from './api.ts'

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

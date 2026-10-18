import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'

test('native audit navigation never replaces or falls back to the legacy offline shell', async () => {
  const handlers = new Map<string, (event: unknown) => void>()
  const cache = new Map<string, Response>([['/', new Response('legacy shell')]])
  let offline = false
  vm.runInNewContext(readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8'), {
    self: {location: {origin: 'https://ppap.example'}, addEventListener: (name: string, callback: (event: unknown) => void) => handlers.set(name, callback)},
    URL, Request, Response,
    fetch: async () => {if (offline) throw new Error('offline'); return new Response('native audit shell')},
    caches: {open: async () => ({put: async (key: string, response: Response) => {cache.set(key, response)}}), match: async (key: string) => cache.get(key)?.clone()},
  })
  for (const path of ['/audit?task=a', '/audit/', '/audit.html?report=pdf', '/__audit-native?view=usage', '/enter-current?return_to=%2Faudit', '/enter-legacy']) {
    for (offline of [false, true]) {
      const request = new Request('https://ppap.example' + path, {headers: {accept: 'text/html'}})
      Object.defineProperties(request, {mode: {value: 'navigate'}, destination: {value: 'document'}})
      let intercepted = false
      handlers.get('fetch')!({request, respondWith: () => {intercepted = true}})
      assert.equal(intercepted, false, `${path} must remain network-only`)
      assert.equal(await cache.get('/')!.clone().text(), 'legacy shell')
    }
  }
  const legacyRequest = new Request('https://ppap.example/', {headers: {accept: 'text/html'}})
  Object.defineProperties(legacyRequest, {mode: {value: 'navigate'}, destination: {value: 'document'}})
  let fallback: Promise<Response> | undefined
  handlers.get('fetch')!({request: legacyRequest, respondWith: (response: Promise<Response>) => {fallback = response}})
  assert.ok(fallback, 'the existing legacy offline shell is still handled')
  assert.equal(await (await fallback).text(), 'legacy shell')
})

test('a redirected navigation cannot replace the legacy offline shell', async () => {
  const handlers = new Map<string, (event: unknown) => void>()
  const cache = new Map<string, Response>([['/', new Response('legacy shell')]])
  const redirected = new Response('native audit shell')
  Object.defineProperties(redirected, {type: {value: 'basic'}, redirected: {value: true}, url: {value: 'https://ppap.example/audit?task=a'}})
  vm.runInNewContext(readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8'), {
    self: {location: {origin: 'https://ppap.example'}, addEventListener: (name: string, callback: (event: unknown) => void) => handlers.set(name, callback)},
    URL, Request, Response,
    fetch: async () => redirected,
    caches: {open: async () => ({put: async (key: string, response: Response) => {cache.set(key, response)}})},
  })
  const request = new Request('https://ppap.example/', {headers: {accept: 'text/html'}})
  Object.defineProperties(request, {mode: {value: 'navigate'}, destination: {value: 'document'}})
  let navigation: Promise<Response> | undefined
  handlers.get('fetch')!({request, respondWith: (response: Promise<Response>) => {navigation = response}})
  assert.ok(navigation)
  assert.equal(await (await navigation).text(), 'native audit shell')
  assert.equal(await cache.get('/')!.clone().text(), 'legacy shell')
})

import assert from 'node:assert/strict'
import test, {afterEach} from 'node:test'
import {modelConfigFailure, readPersonalModelConfig, revokePersonalModelConfig, savePersonalModelConfig} from './model-config-contract.ts'

const originalFetch = globalThis.fetch
Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})
afterEach(() => {globalThis.fetch = originalFetch})
const config = {provider: 'responses-compatible', base_url: 'https://models.company.test/v1', model: 'internal-reasoner', configured: true, revision: '7a7cceab-e503-477a-9874-d0300f7d2dcc', updated_at: '2026-11-06T00:00:00Z'}

test('custom model saves a complete replacement only to Product API and reads back metadata', async () => {
  const calls: {url: string; method: string; body: unknown}[] = []
  globalThis.fetch = async (url, init) => {
    calls.push({url: String(url), method: init!.method!, body: init?.body ? JSON.parse(String(init.body)) : null})
    return new Response(JSON.stringify(config))
  }
  const key = 'synthetic-test-key'
  assert.deepEqual(await savePersonalModelConfig(key, config.base_url, config.model), config)
  assert.deepEqual(await readPersonalModelConfig(), config)
  assert.deepEqual(calls, [
    {url: '/me/model-config', method: 'PUT', body: {api_key: key, base_url: config.base_url, model: config.model}},
    {url: '/me/model-config', method: 'GET', body: null},
  ])
  assert.throws(() => savePersonalModelConfig('', config.base_url, config.model), /完整填写/)
  assert.throws(() => savePersonalModelConfig(key, '', config.model), /完整填写/)
  assert.throws(() => savePersonalModelConfig(key, config.base_url, ''), /完整填写/)
  assert.equal(calls.length, 2)
})

test('lost saves and revocations never retry, and explicit readback is GET only', async () => {
  const calls: string[] = []
  globalThis.fetch = async (_url, init) => {
    calls.push(init!.method!)
    if (init?.method !== 'GET') throw new Error('network reply lost')
    return new Response(JSON.stringify(config))
  }
  await assert.rejects(savePersonalModelConfig('synthetic', config.base_url, config.model), /reply lost/)
  assert.deepEqual(calls, ['PUT'])
  assert.deepEqual(await readPersonalModelConfig(), config)
  await assert.rejects(revokePersonalModelConfig(), /reply lost/)
  assert.deepEqual(calls, ['PUT', 'GET', 'DELETE'])
})

test('validation messages never echo arbitrary server errors or submitted key', () => {
  const secret = 'synthetic-secret-must-not-render'
  assert.equal(modelConfigFailure({code: secret, message: secret}), null)
  assert.equal(modelConfigFailure(new Error(secret)), null)
  for (const code of ['invalid_model_credential', 'invalid_model_endpoint', 'invalid_model_model', 'model_endpoint_not_allowed', 'model_credential_encryption_unavailable']) {
    const message = modelConfigFailure({code, message: secret})
    assert.ok(message)
    assert.equal(message.includes(secret), false)
  }
})

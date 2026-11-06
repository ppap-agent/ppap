import assert from 'node:assert/strict'
import test from 'node:test'
import {checkedUpgrade, nativeCheckerUpgradeAdapter, savedUpgrade, upgradeAddress, upgradeAvailable, type UpgradeChecker} from './checker-upgrade-contract.ts'
import type {CheckerRevision} from './checker-contract.ts'

Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})
const input: UpgradeChecker = {operation_id: 'upgrade/a', source_revision_id: 'enterprise-a', target_revision_id: 'official-b', expected_revision: 3}
const candidate = (): CheckerRevision => ({id: 'candidate-a', suite_key: 'suite/a', base_revision_id: input.target_revision_id, upgrade: {...input}} as CheckerRevision)

test('upgrade receipt binds exact candidate, suite, source, target, original operation and publication CAS', () => {
  assert.equal(checkedUpgrade(candidate(), 'suite/a', input).id, 'candidate-a')
  for (const changes of [{id: ''}, {id: input.source_revision_id}, {id: input.target_revision_id}, {suite_key: 'other'}, {base_revision_id: 'old'}, {upgrade: null}]) assert.throws(() => checkedUpgrade({...candidate(), ...changes}, 'suite/a', input), /升级回执/)
  for (const changes of [{operation_id: 'other'}, {source_revision_id: 'other'}, {target_revision_id: 'other'}, {expected_revision: 4}]) assert.throws(() => checkedUpgrade({...candidate(), upgrade: {...input, ...changes}}, 'suite/a', input), /升级回执/)
})

test('refresh preserves a complete upgrade identity and never borrows another suite or malformed CAS', () => {
  const original = new URL('https://ppap.example/audit?round=r&checkerOptimization=original#result')
  const pending = {scope: 'round:file:clause:suite/a', suite: 'suite/a', input}
  const saved = upgradeAddress(original, pending)
  assert.deepEqual(savedUpgrade(saved, 'suite/a', pending.scope), pending)
  assert.equal(savedUpgrade(saved, 'other', pending.scope), null)
  assert.equal(savedUpgrade(saved, 'suite/a', 'other-scope'), null)
  assert.equal(saved.searchParams.get('checkerOptimization'), 'original')
  assert.equal(saved.hash, '#result')
  assert.equal(upgradeAddress(saved, null).href, original.href)
  for (const value of ['', '-1', 'NaN', '1.5', '1e3', '9007199254740992']) {
    const broken = new URL(saved); broken.searchParams.set('checkerUpgradeExpected', value)
    assert.equal(savedUpgrade(broken, 'suite/a', pending.scope), null)
  }
  for (const field of ['checkerUpgrade', 'checkerUpgradeSource', 'checkerUpgradeTarget']) {
    const broken = new URL(saved); broken.searchParams.delete(field)
    assert.equal(savedUpgrade(broken, 'suite/a', pending.scope), null)
  }
})

test('lost upgrade response only reads the original operation and never submits another upgrade or paid optimization', async () => {
  const original = globalThis.fetch, calls: {method: string | undefined; url: string; body: unknown}[] = []
  globalThis.fetch = async (url, init) => {
    calls.push({method: init?.method, url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null})
    if (init?.method === 'POST') throw new Error('reply lost')
    return new Response(JSON.stringify(candidate()), {headers: {'Content-Type': 'application/json'}})
  }
  try {
    await assert.rejects(nativeCheckerUpgradeAdapter.upgrade('suite/a', input), /reply lost/)
    await nativeCheckerUpgradeAdapter.read('suite/a', input)
    assert.deepEqual(calls, [
      {method: 'POST', url: '/suites/suite%2Fa/upgrades', body: input},
      {method: 'GET', url: '/suites/suite%2Fa/upgrades/upgrade%2Fa', body: null},
    ])
  } finally {globalThis.fetch = original}
})

test('notice acknowledgment names the displayed official revision and cannot publish or upgrade', async () => {
  const original = globalThis.fetch, calls: unknown[] = []
  globalThis.fetch = async (url, init) => {calls.push([init?.method, String(url), JSON.parse(String(init?.body))]); return new Response('{}', {headers: {'Content-Type': 'application/json'}})}
  try {
    await nativeCheckerUpgradeAdapter.seen('suite/a', 'official-b')
    assert.deepEqual(calls, [['POST', '/suites/suite%2Fa/official-update/seen', {official_revision_id: 'official-b'}]])
  } finally {globalThis.fetch = original}
})

test('upgrade availability follows the nearest official ancestor, including descendants of an upgrade', () => {
  const active = {suite_key: 'suite/a', official_update_available: true, latest_official_revision_id: 'official-b'}
  const source = {suite_key: 'suite/a', id: 'enterprise-a', tenant_id: 'tenant-a', official_base_revision_id: 'official-a'}
  assert.equal(upgradeAvailable(active, source), true)
  assert.equal(upgradeAvailable(active, {...source, id: 'edited-upgrade', official_base_revision_id: 'official-b'}), false)
  assert.equal(upgradeAvailable(active, {...source, official_base_revision_id: null}), false)
  assert.equal(upgradeAvailable(active, {...source, suite_key: 'other'}), false)
  assert.equal(upgradeAvailable({...active, official_update_available: false}, source), true)
  assert.equal(upgradeAvailable({...active, official_update_available: false}, {...source, tenant_id: null}), false)
  assert.equal(upgradeAvailable({...active, latest_official_revision_id: null}, source), false)
})

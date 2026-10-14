import assert from 'node:assert/strict'
import test from 'node:test'
import {previewResponse} from '../preview/fixtures.ts'

test('preview uses synthetic identity and never accepts writes', () => {
  assert.equal(previewResponse('GET', '/me', '')?.status, 200)
  assert.equal(previewResponse('POST', '/drafts', '')?.status, 403)
  assert.equal(previewResponse('PUT', '/files/example/blob', '')?.status, 403)
  assert.equal(previewResponse('GET', '/not-an-api', ''), null)
})

test('preview scenes and list filters stay deterministic', () => {
  const populated = previewResponse('GET', '/rounds?q=尺寸', 'ppap_preview_scene=populated')?.body as {rounds: unknown[]}
  const empty = previewResponse('GET', '/rounds', 'ppap_preview_scene=empty')?.body as {rounds: unknown[]}
  const long = previewResponse('GET', '/rounds', 'ppap_preview_scene=long')?.body as {rounds: unknown[]}
  assert.equal(populated.rounds.length, 4)
  assert.equal(empty.rounds.length, 0)
  assert.equal(long.rounds.length, 18)
  assert.equal(previewResponse('GET', '/rules', 'ppap_preview_scene=error')?.status, 503)
})

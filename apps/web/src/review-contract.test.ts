import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from './generated/openapi'
import {reviewOf, reviewMatches} from './review-contract.ts'

type Round = components['schemas']['Round']
const request = {item_key: 'coverage:version-a:1', judgment: 'manual-satisfied', rationale: '核对原件', next_step: '保留记录', version: 0, citations: [{file_version_id: 'version-a', location: '审核 A1', excerpt: '每班'}]}
const round = {findings: [], coverage: {rule_items: [{id: '1', file_version_id: 'version-a', coverage: 'unsupported', review: {...request, version: 1}}]}} as unknown as Round

test('a coverage review uses its original key and confirms every submitted field and revision', () => {
  assert.equal(reviewOf(round, request.item_key)?.version, 1)
  assert.equal(reviewMatches(round, request), true)
  for (const patch of [{next_step: '不同建议'}, {rationale: '不同理由'}, {judgment: 'unknown'}, {version: 2}, {citations: []}]) {
    const changed = structuredClone(round)
    Object.assign(changed.coverage.rule_items![0].review!, patch)
    assert.equal(reviewMatches(changed, request), false)
  }
  assert.equal(reviewMatches(round, {...request, item_key: 'another-key'}), false)
})

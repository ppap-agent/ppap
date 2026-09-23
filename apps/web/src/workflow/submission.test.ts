import assert from 'node:assert/strict'
import test from 'node:test'
import {beginDraftAccept, beginDraftSave, beginReviewSave, finishDraft, markReviewClean, markReviewDirty} from './submission.ts'

test('draft save and accept do not overlap', () => {
  assert.equal(beginDraftSave('idle'), 'saving')
  assert.equal(beginDraftAccept('saving'), null)
  assert.equal(beginDraftSave('accepting'), null)
  assert.equal(beginDraftAccept('idle'), 'accepting')
  assert.equal(beginDraftAccept('accepting'), null)
  assert.equal(finishDraft('saving'), 'idle')
  assert.equal(finishDraft('accepting'), 'idle')
  assert.equal(markReviewClean(), 'clean')
})

test('review save starts only from a dirty draft', () => {
  assert.equal(beginReviewSave('clean'), null)
  assert.equal(beginReviewSave('dirty'), 'saving')
  assert.equal(beginReviewSave('saving'), null)
  assert.equal(markReviewDirty('saving'), 'saving')
  assert.equal(markReviewDirty('clean'), 'dirty')
})

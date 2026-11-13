import assert from 'node:assert/strict'
import test from 'node:test'

import {appendStep, LIVE_STEP_WINDOW, NO_STEPS} from './live-steps.ts'

test('only the latest ten steps stay on screen while every step is counted', () => {
  let steps = NO_STEPS
  for (let index = 0; index < 13; index++) steps = appendStep(steps, {kind: 'run', text: `step ${index}`})
  assert.equal(steps.count, 13)
  assert.equal(steps.items.length, LIVE_STEP_WINDOW)
  assert.deepEqual(steps.items.map(step => step.text), Array.from({length: 10}, (_, index) => `step ${index + 3}`))
  // Ids keep counting, so a step never reuses an earlier step's key.
  assert.deepEqual([steps.items[0].id, steps.items.at(-1)!.id], [3, 12])
  assert.deepEqual(NO_STEPS, {items: [], count: 0})
})

import assert from 'node:assert/strict'
import test from 'node:test'

import {appendStep, LIVE_STEP_WINDOW, NO_STEPS, type LiveSteps} from './live-steps.ts'
import type {TaskActivity} from './task-events.ts'

const play = (steps: TaskActivity[], from: LiveSteps = NO_STEPS) => steps.reduce(appendStep, from)

test('consecutive identical actions share a row and every step is counted', () => {
  const steps = play([
    {action: 'parse_sheet', subject: 'cp.xlsx', target: 'a'},
    ...[1, 2, 3].map(index => ({action: 'view_image' as const, subject: `image${index}.png`, target: 'a'})),
    {action: 'view_image', subject: 'image1.png', target: 'a'},
    {action: 'note', subject: '第 3 条维持 NG。', target: 'a'},
    {action: 'note', subject: '第 4 条改为 PASS。', target: 'a'},
  ])
  assert.equal(steps.count, 7)
  assert.deepEqual(steps.items.map(row => [row.action, row.times, row.subjects]), [
    ['parse_sheet', 1, ['cp.xlsx']],
    ['view_image', 4, ['image1.png', 'image2.png', 'image3.png']],
    ['note', 1, ['第 3 条维持 NG。']],
    ['note', 1, ['第 4 条改为 PASS。']],
  ])
})

test('only the latest rows stay on screen, and a new file finishes the previous one', () => {
  let steps = play(Array.from({length: 13}, (_, index) => ({action: index % 2 ? 'search' as const : 'run_check' as const, target: 'a'})))
  assert.equal(steps.items.length, LIVE_STEP_WINDOW)
  assert.deepEqual([steps.items[0].id, steps.items.at(-1)!.id], [3, 12])
  assert.deepEqual([steps.current, steps.finished, steps.perTarget], ['a', [], {a: 13}])
  steps = play([{action: 'read_file', subject: 'pfmea.xlsx', target: 'b'}, {action: 'record'}], steps)
  // A step without a file belongs to the file under review.
  assert.deepEqual([steps.current, steps.finished, steps.perTarget], ['b', ['a'], {a: 13, b: 2}])
  assert.deepEqual(NO_STEPS, {items: [], count: 0, perTarget: {}, finished: []})
})

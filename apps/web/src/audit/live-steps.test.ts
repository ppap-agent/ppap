import assert from 'node:assert/strict'
import test from 'node:test'

import {appendStep, LIVE_STEP_WINDOW, NO_STEPS, type LiveSteps} from './live-steps.ts'
import type {TaskActivity} from './task-events.ts'

const play = (steps: TaskActivity[], from: LiveSteps = NO_STEPS) => steps.reduce(appendStep, from)

test('consecutive identical actions share a row and every step is counted', () => {
  const steps = play([
    {action: 'read_sheet', subject: 'cp.xlsx', target: 'a'},
    ...[1, 2, 3].map(index => ({action: 'view_image' as const, subject: `image${index}.png`, target: 'a'})),
    {action: 'view_image', subject: 'image1.png', target: 'a'},
    {action: 'load_rules', target: 'a'},
  ])
  assert.equal(steps.count, 6)
  assert.deepEqual(steps.items.map(row => [row.action, row.times, row.subjects]), [
    ['read_sheet', 1, ['cp.xlsx']],
    ['view_image', 4, ['image1.png', 'image2.png', 'image3.png']],
    ['load_rules', 1, []],
  ])
})

test('only the latest rows stay on screen, and a step without a file belongs to the file under review', () => {
  let steps = play(Array.from({length: 13}, (_, index) => ({action: index % 2 ? 'read_file' as const : 'read_sheet' as const, target: 'a'})))
  assert.equal(steps.items.length, LIVE_STEP_WINDOW)
  assert.deepEqual([steps.items[0].id, steps.items.at(-1)!.id], [3, 12])
  steps = play([{action: 'read_file', subject: 'pfmea.xlsx', target: 'b'}, {action: 'load_rules'}], steps)
  assert.deepEqual([steps.current, steps.items.at(-1)!.target], ['b', 'b'])
  assert.deepEqual(NO_STEPS, {items: [], count: 0})
})

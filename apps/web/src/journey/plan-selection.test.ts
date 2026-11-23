import assert from 'node:assert/strict'
import test from 'node:test'
import {initialSelection, planRequest, toggle} from './plan-selection.ts'

const plan = [
  {id: 'revision', external: false, depends_on: [], body: {r: 1}},
  {id: 'clarified', external: false, depends_on: ['revision'], body: {c: 1}},
  {id: 'readings', external: false, depends_on: ['clarified'], body: {x: 1}},
]
const asking = [{id: 'revision', external: false, depends_on: [], body: {}}, {id: 'cards', external: true, depends_on: ['revision'], body: {}}]

test('internal steps start checked and a step the customer sees does not', () => {
  assert.deepEqual([...initialSelection(plan)], ['revision', 'clarified', 'readings'])
  assert.deepEqual([...initialSelection(asking)], ['revision'])
})

test('the selection stays closed under the steps it depends on', () => {
  const all = initialSelection(plan)
  assert.deepEqual([...toggle(plan, all, 'revision', false)], [])
  assert.deepEqual([...toggle(plan, all, 'readings', false)].sort(), ['clarified', 'revision'])
  assert.deepEqual([...toggle(plan, new Set(), 'readings', true)].sort(), ['clarified', 'readings', 'revision'])
})

test('the request carries the checked steps in order, and the rest with the reason', () => {
  const chosen = toggle(plan, initialSelection(plan), 'readings', false)
  assert.deepEqual(planRequest(plan, chosen, ' 解读再看看 '), {
    steps: [{step: 'revision', body: {r: 1}}, {step: 'clarified', body: {c: 1}}],
    unselected: ['readings'],
    reason: '解读再看看',
  })
  assert.deepEqual(planRequest(plan, initialSelection(plan), 'ignored'), {steps: plan.map(step => ({step: step.id, body: step.body})), unselected: []})
})

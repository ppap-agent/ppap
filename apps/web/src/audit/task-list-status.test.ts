import assert from 'node:assert/strict'
import test from 'node:test'
import {taskListStatus} from './task-list-status.ts'

const scope = JSON.stringify(['PFMEA', null])
const other = JSON.stringify(['PFMEA', 'completed'])
const initial = {readScope: null, errorScope: null, busy: false, error: ''}

test('the first read and a changed query/filter are pending before debounce starts', () => {
  assert.deepEqual(taskListStatus(initial, scope), {ready: false, error: '', busy: true})
  assert.deepEqual(taskListStatus({...initial, readScope: other}, scope), {ready: false, error: '', busy: true})
})

test('only the current scope success permits an empty result', () => {
  assert.deepEqual(taskListStatus({...initial, busy: true}, scope), {ready: false, error: '', busy: true})
  assert.deepEqual(taskListStatus({...initial, readScope: scope}, scope), {ready: true, error: '', busy: false})
})

test('a current error is not an empty result, with or without cached success', () => {
  for (const readScope of [null, other, scope]) {
    assert.deepEqual(taskListStatus({...initial, readScope, error: 'failed', errorScope: scope}, scope), {ready: false, error: 'failed', busy: false})
  }
})

test('errors from another query cannot mask pending or successful current results', () => {
  assert.deepEqual(taskListStatus({...initial, error: 'failed', errorScope: other}, scope), {ready: false, error: '', busy: true})
  assert.deepEqual(taskListStatus({...initial, readScope: scope, error: 'failed', errorScope: other}, scope), {ready: true, error: '', busy: false})
})

test('a refresh retains successful scope evidence but remains busy until settled', () => {
  assert.deepEqual(taskListStatus({...initial, readScope: scope, busy: true}, scope), {ready: true, error: '', busy: true})
})

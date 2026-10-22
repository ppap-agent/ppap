import assert from 'node:assert/strict'
import test from 'node:test'
import {workbenchItems} from './workbench-items.ts'
import type {components} from '../generated/openapi.ts'
type Task = components['schemas']['AuditTaskSummary']
const completed = {id: 't', number: 'PPAP-1', state: 'completed', round_id: 'r', analysis_mode: 'standard', execution: 'completed', updated_at: '2026-10-21', result_counts: {NG: 2}, reviewed_count: 0, uncovered_count: 0,
  files: ['a', 'b'].map(id => ({selection: 'selected', file: {id, original_filename: '同名.xlsx'}}))} as Task
test('completed NG is not a mandatory manual-review queue; every file identity remains visible', () => {
  const row = workbenchItems([completed])[0]
  assert.equal(row.task, 'completed')
  assert.deepEqual(row.review, {saved: 0, total: 2})
  assert.equal(row.files?.length, 2)
  assert.equal(row.href, '/tasks/t')
  assert.equal(workbenchItems([{...completed, execution: 'failed'}])[0].task, 'attention')
})
test('only actual task-round identity removes duplicate history; names never deduplicate files or tasks', () => {
  const rounds = [{id: 'r', task_id: 't'}, {id: 'another', task_id: 'not-loaded'}] as components['schemas']['RoundSummary'][]
  assert.equal(workbenchItems([completed], [], rounds).length, 1)
  assert.equal(workbenchItems([completed, {...completed, id: 't2', round_id: 'r2'}]).length, 2)
})
test('legacy round activity controls workbench recency without changing its identity', () => {
  const round = {id: 'old', execution: 'completed', inputs: [], result_counts: {}, created_at: '2026-09-01', updated_at: '2026-10-21'} as components['schemas']['RoundSummary']
  const item = workbenchItems([], [], [round])[0]
  assert.equal(item.updatedAt, '2026-10-21')
  assert.equal(item.href, '/audit?round=old')
})
test('execution faults use an independent queue flag without reclassifying NG or uncovered results', () => {
  for (const fault of [{has_execution_error: true}, {result_counts: {ERR: 1}}]) {
    const task = {...completed, ...fault}
    const item = workbenchItems([task])[0]
    assert.equal(item.task, 'attention')
    assert.equal(item.state, '存在执行异常')
    assert.equal(item.next, '查看异常')
    assert.equal(task.execution, 'completed')
    const round = {id: 'old', execution: 'completed', has_execution_error: true, inputs: [], result_counts: {}, created_at: '2026-09-01'} as components['schemas']['RoundSummary']
    assert.equal(workbenchItems([], [], [round])[0].task, 'attention')
    assert.equal(workbenchItems([], [], [round])[0].next, '查看异常')
    assert.equal(round.execution, 'completed')
  }
  assert.equal(workbenchItems([{...completed, has_execution_error: false, result_counts: {NG: 2, UNABLE: 1}, uncovered_count: 3}])[0].task, 'completed')
  assert.equal(workbenchItems([{...completed, has_execution_error: false, result_counts: {ERR: 1}}])[0].task, 'completed')
  assert.equal(workbenchItems([{...completed, state: 'reviewing', execution: 'running', result_counts: {ERR: 1}}])[0].task, 'running')
  const running = {id: 'old', execution: 'running', inputs: [], result_counts: {ERR: 1}, created_at: '2026-09-01'} as components['schemas']['RoundSummary']
  assert.equal(workbenchItems([], [], [running])[0].task, 'running')
  assert.equal(workbenchItems([], [], [running])[0].next, '查看进度')
  assert.equal(workbenchItems([], [], [{...running, execution: 'failed'}])[0].task, 'attention')
  assert.equal(workbenchItems([], [], [{...running, execution: 'failed'}])[0].next, '查看异常')
})

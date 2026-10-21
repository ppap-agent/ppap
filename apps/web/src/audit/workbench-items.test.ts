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

import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi.ts'
import {reconcileTaskSummary, summarizeTask, taskIsBehindSummary} from './task-summary.ts'

test('task summaries preserve every file identity and selection without result bodies', () => {
  const task: components['schemas']['AuditTask'] = {id: 'task', number: 'PPAP-1', state: 'pending_confirm', analysis_mode: 'standard', created_at: '2026-10-01', updated_at: '2026-10-01', round_id: null, completion_reason: null, source_draft_id: 'task', customer_preference: null, estimated_points: 0, input_revision: 2,
    files: ['selected', 'removed', 'unselected'].map((selection, index) => ({file: {id: `version-${index}`, file_id: `file-${index}`, original_filename: 'same.xlsx', content_type: 'application/octet-stream', byte_size: 1, status: 'ready', sha256: null}, selection: selection as 'selected' | 'removed' | 'unselected', removal_reason: null, match: null, validation: {state: 'valid', reasons: []}}))}
  const summary = summarizeTask(task)
  assert.deepEqual(summary.files.map(row => row.file.id), ['version-0', 'version-1', 'version-2'])
  assert.deepEqual(summary.files.map(row => row.selection), ['selected', 'removed', 'unselected'])
  assert.equal(Object.hasOwn(summary.files[0], 'validation'), false)
  const completed = {...summary, state: 'completed' as const}
  assert.equal(reconcileTaskSummary(completed, summary), completed)
  assert.equal(reconcileTaskSummary(completed, {...summary, input_revision: 3}).input_revision, 3)
  assert.equal(reconcileTaskSummary(summary, {...completed, input_revision: 1}), summary)
  // Summary and detail observation clocks are not interchangeable.
  assert.equal(reconcileTaskSummary({...summary, updated_at: '2026-10-19'}, completed).state, 'completed')
  assert.equal(taskIsBehindSummary(task, {...summary, input_revision: 3}), true)
  assert.equal(taskIsBehindSummary(task, completed), true)
  assert.equal(taskIsBehindSummary(task, summary), false)
})

import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi.ts'
import {reconcileTaskSummary, summarizeTask, taskIsBehindSummary} from './task-summary.ts'
import {workbenchItems} from './workbench-items.ts'

test('task summaries preserve every file identity and selection without result bodies', () => {
  const task: components['schemas']['AuditTask'] = {confirmation_basis_digest: null, confirmation_scope: null, id: 'task', number: 'PPAP-1', state: 'pending_confirm', analysis_mode: 'standard', created_at: '2026-10-01', updated_at: '2026-10-01', round_id: null, completion_reason: null, source_draft_id: 'task', customer_preference: null, estimated_points: 0, input_revision: 2,
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
test('opening partial execution faults preserves the Round terminal state and workbench queue', () => {
  const base = {id: 't', number: 'PPAP-1', state: 'completed', round_id: 'r', analysis_mode: 'standard', created_at: '2026-10-22', updated_at: '2026-10-22', completion_reason: 'processed', execution: 'completed', has_execution_error: true,
    files: [{selection: 'selected', file: {id: 'v', original_filename: 'a.xlsx'}, audit: {execution: 'review_failed', checks: [{execution: 'failed', coverage: 'execution_error', finding: null}]}}]} as unknown as components['schemas']['AuditTask']
  const summarized = summarizeTask(base)
  assert.equal(summarized.execution, 'completed')
  assert.equal(summarized.has_execution_error, true)
  assert.equal(workbenchItems([summarized])[0].task, 'attention')
  const legacy = {...base}; delete legacy.execution; delete legacy.has_execution_error
  assert.equal(summarizeTask(legacy).execution, undefined)
  assert.equal(summarizeTask(legacy).has_execution_error, true)
  const loading = {...legacy, state: 'reviewing' as const, files: []}
  assert.equal(summarizeTask(loading).execution, undefined)
  assert.equal(workbenchItems([summarizeTask(loading)])[0].task, 'running')
  const explicit = {...base, has_execution_error: false}
  assert.equal(summarizeTask(explicit).has_execution_error, false)
  assert.equal(workbenchItems([summarizeTask(explicit)])[0].task, 'completed')
  const running = {...legacy, state: 'reviewing' as const, execution: 'running' as const}
  assert.equal(summarizeTask(running).has_execution_error, false)
  assert.equal(workbenchItems([summarizeTask(running)])[0].task, 'running')
})

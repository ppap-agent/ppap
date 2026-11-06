import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi.ts'
import {createTaskRecognition, type AuditRecognitionAdapter} from './task-recognition.ts'

type Task = components['schemas']['AuditTask']
const files: Task['files'] = [{file: {id: 'version-a', file_id: 'file-a', original_filename: 'a.xlsx', content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', byte_size: 10, status: 'ready', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: 'valid', reasons: []}, match: null}]
const input = () => ({files: structuredClone(files), mode: 'standard' as const, customer: null, previous: null})
function task(requestId: string): Task {
  return {confirmation_basis_digest: null, confirmation_scope: null, id: requestId, number: 'PPAP-20261016-001', state: 'matching', analysis_mode: 'standard', customer_preference: null, source_draft_id: null, round_id: null, completion_reason: null, estimated_points: null, created_at: '2026-10-16T00:00:00Z', updated_at: '2026-10-16T00:00:00Z', files: structuredClone(files), input_revision: 0, recognition_request_id: requestId}
}

test('a prepared draft retains its own task identity when the recognition reply is lost', async () => {
  const commands: unknown[] = [], reads: unknown[] = [], accepted: Task[] = []
  const preparation = {id: 'prepared-draft', revision: 4}
  const recognition = createTaskRecognition({
    match: async (_files, _mode, _customer, _previous, requestId, _predecessor, source) => {
      commands.push({requestId, source}); throw new Error('reply lost')
    },
    readRecognition: async (taskId, requestId) => {
      reads.push({taskId, requestId})
      return {task_id: taskId, request_id: requestId, state: 'accepted', input_revision: 0, task: {...task(requestId), id: preparation.id, source_draft_id: preparation.id}}
    },
  }, next => accepted.push(next))
  await recognition.start('local-preparation', {...input(), preparation})
  const original = recognition.get('local-preparation')!
  assert.equal(original.taskId, preparation.id)
  assert.notEqual(original.requestId, preparation.id)
  await recognition.start('local-preparation', {...input(), preparation})
  await recognition.read('local-preparation')
  assert.deepEqual(commands, [{requestId: original.requestId, source: preparation}])
  assert.deepEqual(reads, [{taskId: preparation.id, requestId: original.requestId}])
  assert.equal(accepted[0].id, preparation.id)
  assert.equal(recognition.get('local-preparation')!.phase, 'accepted')
})

test('a lost recognition reply retains its input and can only read the original request', async () => {
  const requests: string[] = [], receipts: string[] = [], accepted: Task[] = []
  const adapter: AuditRecognitionAdapter = {
    match: async (_files, _mode, _customer, _previous, requestId) => {requests.push(requestId); throw new Error('reply lost')},
    readRecognition: async (taskId, requestId) => {receipts.push(requestId); return {task_id: taskId, request_id: requestId, state: 'accepted', input_revision: 0, task: task(requestId)}},
  }
  const recognition = createTaskRecognition(adapter, next => accepted.push(next))
  const draft = input()
  await recognition.start('draft-a', draft)
  const unknown = recognition.get('draft-a')!
  assert.equal(unknown.phase, 'unknown')
  draft.files[0].selection = 'unselected'
  await recognition.start('draft-a', draft)
  await recognition.read('draft-a')
  assert.deepEqual(receipts, requests)
  assert.equal(requests.length, 1)
  assert.equal(accepted[0].files[0].selection, 'selected')
  assert.equal(accepted[0].input_revision, 0)
  assert.equal(recognition.get('draft-a')!.phase, 'accepted')
})

test('recognition keeps a service trace with its own unresolved request and clears it after recovery', async () => {
  let reads = 0
  const recognition = createTaskRecognition({
    match: async (_files, _mode, customer) => {throw customer === 'A' ? Object.assign(new Error('unavailable'), {traceId: 'recognition-service-a'}) : new TypeError('offline')},
    readRecognition: async (taskId, requestId) => ++reads === 1 ? {task_id: taskId, request_id: requestId, state: 'unknown', input_revision: null, task: null} : {task_id: taskId, request_id: requestId, state: 'accepted', input_revision: 0, task: {...task(requestId), customer_preference: 'A'}},
  }, () => {})
  await recognition.start('draft-a', {...input(), customer: 'A'})
  const failed = recognition.get('draft-a')!
  assert.equal(failed.phase, 'unknown')
  assert.equal(failed.traceId, 'recognition-service-a')
  assert.notEqual(failed.requestId, 'recognition-service-a')
  await recognition.start('draft-b', input())
  assert.equal('traceId' in recognition.get('draft-b')!, false)
  await recognition.read('draft-a')
  assert.equal(recognition.get('draft-a')!.traceId, 'recognition-service-a')
  assert.equal(recognition.get('draft-a')!.requestId, failed.requestId)
  await recognition.read('draft-a')
  assert.equal(recognition.get('draft-a')!.phase, 'accepted')
  assert.equal('traceId' in recognition.get('draft-a')!, false)
  assert.equal(recognition.get('draft-b')!.phase, 'unknown')
})

test('an accepted receipt cannot replace the submitted file snapshot', async () => {
  const accepted: Task[] = []
  const recognition = createTaskRecognition({
    match: async (_files, _mode, _customer, _previous, requestId) => {
      const next = task(requestId)
      next.files[0].file.original_filename = 'different.xlsx'
      return next
    },
    readRecognition: async () => {throw new Error('not read')},
  }, next => accepted.push(next))
  await recognition.start('draft-a', input())
  assert.equal(recognition.get('draft-a')!.phase, 'unknown')
  assert.deepEqual(accepted, [])
})
test('recognition receipt must retain the explicit re-audit source and rule choice', async () => {
  const accepted: Task[] = []
  const recognition = createTaskRecognition({
    match: async (_files, _mode, _customer, _previous, requestId) => task(requestId),
    readRecognition: async () => {throw new Error('not read')},
  }, next => accepted.push(next))
  const draft = {...input(), predecessorId: 'original-round'}
  draft.files[0].preferred_rule_version_id = 'original-rule'
  await recognition.start('re-audit', draft)
  assert.equal(recognition.get('re-audit')!.phase, 'unknown')
  assert.deepEqual(accepted, [])
})

test('recognition accepts the server-normalized removal reason without losing the excluded file', async () => {
  const draft = input()
  draft.files.push({...structuredClone(files[0]), file: {...files[0].file, id: 'version-b', file_id: 'file-b'}, selection: 'removed', removal_reason: '  文件加密  '})
  const accepted: Task[] = []
  const recognition = createTaskRecognition({
    match: async (submitted, _mode, _customer, _previous, requestId) => ({...task(requestId), files: submitted.map(row => ({...row, removal_reason: row.removal_reason?.trim() || null}))}),
    readRecognition: async () => {throw new Error('not read')},
  }, next => accepted.push(next))
  await recognition.start('draft-a', draft)
  assert.equal(recognition.get('draft-a')!.phase, 'accepted')
  assert.equal(accepted[0].files[1].selection, 'removed')
  assert.equal(accepted[0].files[1].removal_reason, '文件加密')
})

test('an accepted request superseded by another input revision has an explicit conflict', async () => {
  const accepted: Task[] = []
  const recognition = createTaskRecognition({
    match: async () => {throw new Error('reply lost')},
    readRecognition: async (taskId, requestId) => ({task_id: taskId, request_id: requestId, state: 'accepted', input_revision: 0, task: {...task(requestId), input_revision: 1, recognition_request_id: 'another-request'}}),
  }, next => accepted.push(next))
  await recognition.start('draft-a', input())
  await recognition.read('draft-a')
  assert.equal(recognition.get('draft-a')!.phase, 'conflict')
  assert.equal(recognition.get('draft-a')!.currentRevision, 1)
  assert.deepEqual(accepted, [])
})

test('an accepted recognition receipt for an ended task has a current-task conflict exit', async () => {
  const recognition = createTaskRecognition({
    match: async () => {throw new Error('reply lost')},
    readRecognition: async (taskId, requestId) => ({task_id: taskId, request_id: requestId, state: 'accepted', input_revision: 0, task: {...task(requestId), state: 'completed', completion_reason: 'no_matching_rules'}}),
  }, () => {throw new Error('must retain the draft until the current task is read')})
  await recognition.start('draft-a', input())
  await recognition.read('draft-a')
  assert.equal(recognition.get('draft-a')!.phase, 'conflict')
  assert.equal(recognition.get('draft-a')!.currentRevision, 0)
})

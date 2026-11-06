import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi.ts'
import {createTaskAcceptance} from './task-acceptance.ts'

type Task = components['schemas']['AuditTask']
function pending(digest = 'a'.repeat(64)): Task {
  return {id: 'task-a', number: 'PPAP-1', state: 'pending_confirm', analysis_mode: 'standard', created_at: '2026-11-06', updated_at: '2026-11-06', source_draft_id: 'task-a', round_id: null, customer_preference: null, completion_reason: null, estimated_points: 500, input_revision: 2, confirmation_basis_digest: digest,
    files: [{file: {id: 'file-a', file_id: 'source-a', original_filename: 'a.xlsx', content_type: 'application/octet-stream', byte_size: 3, status: 'ready', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: 'valid', reasons: []}, match: {state: 'matched', metadata: {customer: null, model: null, part: null}, document_type: 'PFMEA', gaps: [], rule: {id: 'rule-a', title: 'PFMEA', body: {kind: 'presence'}}}}]}
}
const accepted = (input: Task, requestId: string): Task => ({...structuredClone(input), confirmation_basis_digest: null, state: 'reviewing', round_id: 'round-a', acceptance: {request_id: requestId, state: 'accepted', reason: null}})
const rejected = (input: Task, requestId: string): Task => ({...structuredClone(input), acceptance: {request_id: requestId, state: 'rejected', reason: '审核依据已变化，请刷新后重新确认。'}})

test('changed confirmation basis requires a refreshed snapshot and another explicit command', async () => {
  const old = pending(), fresh = pending('b'.repeat(64)), commands: {task: Task; requestId: string}[] = [], results: Task[] = []
  fresh.files[0].match!.rule!.body = {kind: 'engine', type: 'PFMEA', customers: [], models: [], parts: [], rules: [{id: '1', category: '新检查', priority: null, rule_to_check: '已发布新规则', type_of_rule: 'table'}]}
  const controller = createTaskAcceptance({
    confirm: async (task, requestId) => {commands.push({task, requestId}); return task.confirmation_basis_digest === old.confirmation_basis_digest ? rejected(fresh, requestId) : accepted(task, requestId)},
    readTask: async () => {throw new Error('not a new confirmation')},
  }, task => results.push(task))
  await controller.confirm(old)
  assert.equal(controller.get(old.id)!.phase, 'rejected')
  assert.equal(controller.needsBasisRefresh(old), true)
  assert.deepEqual(results, [])
  await controller.confirm(old)
  assert.equal(commands.length, 1)
  assert.equal(controller.restore(fresh), true)
  assert.equal(controller.needsBasisRefresh(fresh), false)
  assert.equal(commands.length, 1, 'reading current rules must not start an audit')
  await controller.confirm(fresh)
  assert.equal(commands.length, 2)
  assert.equal(commands[0].task.confirmation_basis_digest, old.confirmation_basis_digest)
  assert.equal(commands[1].task.confirmation_basis_digest, fresh.confirmation_basis_digest)
  assert.notEqual(commands[0].requestId, commands[1].requestId)
  assert.deepEqual(results[0].files, fresh.files)
  assert.equal(controller.get(old.id)!.phase, 'accepted')
})

test('a lost rejection reads the original operation and cannot silently confirm a newer basis', async () => {
  const old = pending(), fresh = pending('b'.repeat(64)), commands: string[] = [], reads: string[] = []
  const controller = createTaskAcceptance({
    confirm: async (_task, requestId) => {commands.push(requestId); throw new TypeError('response lost')},
    readTask: async (id, requestId) => {assert.equal(id, old.id); reads.push(requestId!); return rejected(fresh, requestId!)},
  }, () => assert.fail('not accepted'))
  await controller.confirm(old)
  assert.equal(controller.get(old.id)!.phase, 'unknown')
  await controller.confirm(fresh)
  assert.equal(commands.length, 1)
  await controller.reconcile(fresh)
  assert.deepEqual(reads, commands)
  assert.equal(controller.get(old.id)!.phase, 'rejected')
  assert.equal(controller.needsBasisRefresh(old), true)
})

test('a lost accepted operation retains the seen basis despite later publication and local edits', async () => {
  const input = pending(), commands: Task[] = [], results: Task[] = []
  let frozen: Task
  const controller = createTaskAcceptance({
    confirm: async (task, requestId) => {commands.push(task); frozen = accepted(task, requestId); throw new TypeError('response lost')},
    readTask: async () => frozen,
  }, task => results.push(task))
  await controller.confirm(input)
  input.confirmation_basis_digest = 'b'.repeat(64)
  input.files[0].match!.rule!.title = '后来发布的规则'
  await controller.confirm(input)
  await controller.reconcile(input)
  assert.equal(commands.length, 1)
  assert.equal(commands[0].confirmation_basis_digest, 'a'.repeat(64))
  assert.equal(results[0].files[0].match!.rule!.title, 'PFMEA')
  assert.equal(controller.get(input.id)!.phase, 'accepted')
})

test('accepted receipts cannot replace the rules the user saw even with the same digest', async () => {
  const input = pending()
  const controller = createTaskAcceptance({
    confirm: async (task, requestId) => {const next = accepted(task, requestId); next.files[0].match!.rule!.body = {kind: 'engine', type: 'PFMEA', customers: [], models: [], parts: [], rules: []}; return next},
    readTask: async () => {throw new Error('not read')},
  }, () => assert.fail('mismatched body must not be accepted'))
  await controller.confirm(input)
  assert.equal(controller.get(input.id)!.phase, 'unknown')
})

test('null confirmation basis cannot start a new audit but accepted history remains readable', async () => {
  const input = {...pending(), confirmation_basis_digest: null}
  const controller = createTaskAcceptance({confirm: async () => {throw new Error('must not post')}, readTask: async () => {throw new Error('not read')}}, () => {})
  await controller.confirm(input)
  assert.equal(controller.get(input.id), undefined)
  assert.equal(controller.restore(accepted(input, 'original-request')), true)
  assert.equal(controller.get(input.id)!.phase, 'accepted')
})

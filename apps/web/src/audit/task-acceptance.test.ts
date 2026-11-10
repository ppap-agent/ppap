import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi.ts'
import {checkerSuiteUnavailable, confirmationScope, personalModelRequired, createTaskAcceptance, suiteAdmissionBlocks, suiteVersionConfirmationRequired} from './task-acceptance.ts'

type Task = components['schemas']['AuditTask']
function pending(digest = 'a'.repeat(64)): Task {
  return {id: 'task-a', number: 'PPAP-1', state: 'pending_confirm', analysis_mode: 'standard', created_at: '2026-11-06', updated_at: '2026-11-06', source_draft_id: 'task-a', round_id: null, customer_preference: null, completion_reason: null, estimated_points: 500, input_revision: 2, confirmation_basis_digest: digest, confirmation_scope: {target_file_version_ids: ['file-a'], reference_file_version_ids: [], excluded_files: []},
    files: [{file: {id: 'file-a', file_id: 'source-a', original_filename: 'a.xlsx', content_type: 'application/octet-stream', byte_size: 3, status: 'ready', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: 'valid', reasons: []}, match: {state: 'matched', metadata: {customer: null, model: null, part: null}, document_type: 'PFMEA', gaps: [], rule: {id: 'rule-a', title: 'PFMEA', body: {kind: 'presence'}}}}]}
}
const accepted = (input: Task, requestId: string): Task => ({...structuredClone(input), files: structuredClone(input.files).map(file => {const excluded = input.confirmation_scope?.excluded_files.find(row => row.file_version_id === file.file.id); return excluded ? {...file, selection: 'removed', removal_reason: excluded.reason, audit: null} : file}), confirmation_basis_digest: null, state: 'reviewing', round_id: 'round-a', acceptance: {request_id: requestId, state: 'accepted', reason: null}})
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

test('navigation recovery adopts only the original operation rejection and preserves its stale basis', async () => {
  const old = pending(), fresh = pending('b'.repeat(64)), commands: string[] = []
  const controller = createTaskAcceptance({
    confirm: async (_task, requestId) => {commands.push(requestId); throw new TypeError('response lost')},
    readTask: async () => {throw new Error('navigation already read this receipt')},
  }, () => assert.fail('rejection cannot become acceptance'))
  await controller.confirm(old)
  controller.restore(rejected(fresh, 'another-request'))
  assert.equal(controller.get(old.id)!.phase, 'unknown')
  await controller.confirm(fresh)
  assert.equal(commands.length, 1)
  controller.restore(rejected(fresh, commands[0]))
  assert.equal(controller.get(old.id)!.phase, 'rejected')
  assert.equal(controller.needsBasisRefresh(old), true)
  assert.equal(controller.needsBasisRefresh(fresh), false)
  await controller.confirm(old)
  assert.equal(commands.length, 1)
})

function partial(): Task {
  const task = pending()
  const file = task.files[0]
  task.files.push(
    {...structuredClone(file), file: {...file.file, id: 'file-b', original_filename: '阻断.xlsx'}},
    {...structuredClone(file), file: {...file.file, id: 'file-c'}, match: {...file.match!, state: 'reference', rule: null}},
    {...structuredClone(file), file: {...file.file, id: 'file-d'}, selection: 'unselected'},
    {...structuredClone(file), file: {...file.file, id: 'file-e'}, selection: 'removed', removal_reason: '识别前移除'},
    {...structuredClone(file), file: {...file.file, id: 'file-f'}, match: {...file.match!, state: 'unmatched', rule: null}},
  )
  task.confirmation_scope = {target_file_version_ids: ['file-a'], reference_file_version_ids: ['file-c'], excluded_files: [{file_version_id: 'file-b', reason: '已认证的预检阻断：原件无内容'}, {file_version_id: 'file-e', reason: '识别前移除'}, {file_version_id: 'file-f', reason: '未匹配到规则，不进入本轮审核'}]}
  return task
}

test('partial certified gate uses the complete server scope without rewriting the pending selections', async () => {
  const input = partial(), results: Task[] = []
  assert.deepEqual(confirmationScope(input), input.confirmation_scope)
  const controller = createTaskAcceptance({confirm: async (task, requestId) => accepted(task, requestId), readTask: async () => {throw new Error('not read')}}, task => results.push(task))
  await controller.confirm(input)
  assert.equal(controller.get(input.id)!.phase, 'accepted')
  assert.deepEqual(results[0].files.filter(file => file.selection === 'selected').map(file => file.file.id), ['file-a', 'file-c'])
  assert.equal(results[0].files[1].removal_reason, input.confirmation_scope!.excluded_files[0].reason)
  assert.equal(results[0].files[1].audit, null)
  assert.equal(input.files[1].selection, 'selected')
  assert.equal(input.files[5].selection, 'selected')
})

for (const [label, mutate] of [
  ['scope changed', (task: Task) => {task.confirmation_scope!.target_file_version_ids.push('file-b')}],
  ['scope missing', (task: Task) => {task.confirmation_scope = null}],
  ['scope reason changed', (task: Task) => {task.confirmation_scope!.excluded_files[0].reason = '另一原因'}],
  ['actual reason changed', (task: Task) => {task.files[1].removal_reason = '另一原因'}],
  ['blocked target admitted', (task: Task) => {task.files[1].selection = 'selected'}],
  ['blocked target executed', (task: Task) => {task.files[1].audit = {} as NonNullable<Task['files'][number]['audit']>}],
  ['reference executed', (task: Task) => {task.files[2].audit = {} as NonNullable<Task['files'][number]['audit']>}],
  ['unselected file changed', (task: Task) => {task.files[3].selection = 'removed'}],
  ['original removal changed', (task: Task) => {task.files[4].removal_reason = '另一原因'}],
  ['file omitted', (task: Task) => {task.files.pop()}],
  ['file duplicated', (task: Task) => {task.files[5] = structuredClone(task.files[4])}],
] as const) test(`partial gate receipt rejects ${label}`, async () => {
  const input = partial()
  const controller = createTaskAcceptance({confirm: async (task, requestId) => {const next = accepted(task, requestId); mutate(next); return next}, readTask: async () => {throw new Error('not read')}}, () => assert.fail('changed scope cannot be accepted'))
  await controller.confirm(input)
  assert.equal(controller.get(input.id)!.phase, 'unknown')
})

for (const [label, mutate] of [
  ['duplicate', (task: Task) => {task.confirmation_scope!.target_file_version_ids.push('file-a')}],
  ['overlapping', (task: Task) => {task.confirmation_scope!.reference_file_version_ids.push('file-a')}],
  ['outside', (task: Task) => {task.confirmation_scope!.target_file_version_ids[0] = 'outside'}],
  ['incomplete', (task: Task) => {task.confirmation_scope!.excluded_files.pop()}],
  ['wrong role', (task: Task) => {task.confirmation_scope!.target_file_version_ids[0] = 'file-c'; task.confirmation_scope!.reference_file_version_ids[0] = 'file-a'}],
  ['changed original removal', (task: Task) => {task.confirmation_scope!.excluded_files[1].reason = '另一原因'}],
  ['empty reason', (task: Task) => {task.confirmation_scope!.excluded_files[0].reason = ' '}],
  ['unselected admitted', (task: Task) => {task.confirmation_scope!.target_file_version_ids.push('file-d')}],
] as const) test(`invalid ${label} pending scope cannot start an audit`, async () => {
  const input = partial(); mutate(input)
  assert.equal(confirmationScope(input), null)
  const controller = createTaskAcceptance({confirm: async () => {throw new Error('must not post')}, readTask: async () => {throw new Error('not read')}}, () => {})
  await controller.confirm(input)
  assert.equal(controller.get(input.id), undefined)
})

test('a newly completed gate rejects the old digest before refresh and accepts only the new effective scope', async () => {
  const current = partial(), old = structuredClone(current), results: Task[] = [], commands: string[] = []
  old.confirmation_scope!.target_file_version_ids.push('file-b')
  old.confirmation_scope!.excluded_files.shift()
  current.confirmation_basis_digest = 'b'.repeat(64)
  const controller = createTaskAcceptance({
    confirm: async (task, requestId) => {commands.push(requestId); return task.confirmation_basis_digest === old.confirmation_basis_digest ? rejected(current, requestId) : accepted(task, requestId)},
    readTask: async () => {throw new Error('not read')},
  }, task => results.push(task))
  await controller.confirm(old)
  assert.equal(controller.get(old.id)!.phase, 'rejected')
  await controller.confirm(old)
  assert.equal(commands.length, 1)
  controller.restore(current)
  assert.equal(commands.length, 1)
  await controller.confirm(current)
  assert.notEqual(commands[0], commands[1])
  assert.equal(results[0].files[1].selection, 'removed')
  assert.deepEqual(results[0].confirmation_scope, current.confirmation_scope)
})

test('an all-blocked server scope cannot start an audit even if a stale digest remains', async () => {
  const input = pending()
  input.confirmation_scope = {target_file_version_ids: [], reference_file_version_ids: [], excluded_files: [{file_version_id: 'file-a', reason: '已认证阻断'}]}
  assert.deepEqual(confirmationScope(input), input.confirmation_scope)
  const controller = createTaskAcceptance({confirm: async () => {throw new Error('must not post')}, readTask: async () => {throw new Error('not read')}}, () => {})
  await controller.confirm(input)
  assert.equal(controller.get(input.id), undefined)
})


test('an inspectable stale Suite becomes confirmable after explicit recovery of its current admission', async () => {
  const task = pending(), calls: string[] = [], results: Task[] = []
  task.files[0].suite_admission = {state: 'validation_required', suite_key: 'suite-a', suite_revision_id: 'revision-a', verification_status: 'stale', force_unverified: false, samples_not_rerun: false}
  const controller = createTaskAcceptance({confirm: async (input, id) => {calls.push(id); return accepted(input, id)}, readTask: async () => task}, result => results.push(result))
  assert.deepEqual(suiteAdmissionBlocks(task), [task.files[0]])
  await controller.confirm(task)
  assert.equal(calls.length, 0)
  const current = structuredClone(task)
  current.confirmation_basis_digest = 'b'.repeat(64)
  current.files[0].suite_admission = {...task.files[0].suite_admission, state: 'ready', force_unverified: true}
  controller.restore(current)
  await controller.confirm(current)
  assert.equal(calls.length, 1)
  assert.equal(results[0].acceptance?.state, 'accepted')
  assert.deepEqual(results[0].files[0].match, task.files[0].match)
})

test('the server included scope stays confirmable when a stale Suite belongs to an excluded file', async () => {
  const task = pending(), results: Task[] = []
  task.files.push({...structuredClone(task.files[0]), file: {...task.files[0].file, id: 'file-excluded'}, suite_admission: {state: 'validation_required', suite_key: 'suite-other', suite_revision_id: 'revision-other', verification_status: 'stale', force_unverified: false, samples_not_rerun: false}})
  task.confirmation_scope!.excluded_files.push({file_version_id: 'file-excluded', reason: '预检已确认的阻断缺陷，需修正后重新提交'})
  assert.deepEqual(suiteAdmissionBlocks(task), [])
  const controller = createTaskAcceptance({confirm: async (input, id) => accepted(input, id), readTask: async () => task}, result => results.push(result))
  await controller.confirm(task)
  assert.equal(results[0].acceptance?.state, 'accepted')
  assert.equal(results[0].files[1].selection, 'removed')
})


test('lost stale-Suite refusal settles the original operation before a fresh ready confirmation', async () => {
  const shown = pending(), blocked = pending('b'.repeat(64)), commands: string[] = [], results: Task[] = []
  blocked.files[0].suite_admission = {state: 'validation_required', suite_key: 'suite-a', suite_revision_id: 'revision-a', verification_status: 'stale', force_unverified: false, samples_not_rerun: false}
  let originalId = ''
  const controller = createTaskAcceptance({
    confirm: async (input, id) => {commands.push(id); if (!originalId) {originalId = id; throw new TypeError('refusal response lost')} return accepted(input, id)},
    readTask: async (_id, requestId) => {assert.equal(requestId, originalId); return {...blocked, acceptance: {request_id: requestId!, state: 'rejected', reason: '检查版本需要重新验证，本次未被接收。'}}},
  }, result => results.push(result))
  await controller.confirm(shown)
  assert.equal(controller.get(shown.id)?.phase, 'unknown')
  await controller.reconcile(blocked)
  assert.equal(controller.get(shown.id)?.phase, 'rejected')
  assert.match(controller.get(shown.id)!.message, /重新验证/)
  const ready = {...blocked, confirmation_basis_digest: 'c'.repeat(64), files: blocked.files.map(file => ({...file, suite_admission: {...file.suite_admission!, state: 'ready' as const, verification_status: 'verified' as const}}))}
  controller.restore(ready)
  await controller.confirm(ready)
  assert.equal(commands.length, 2)
  assert.notEqual(commands[1], originalId)
  assert.equal(results[0].acceptance?.request_id, commands[1])
})

test('a missing Suite is reported as an adoption step, not a file recognition failure', () => {
  assert.equal(checkerSuiteUnavailable({gaps: ['metadata_uses_excerpt', 'checker_suite_unavailable']}), true)
  assert.equal(checkerSuiteUnavailable({gaps: ['metadata_uses_excerpt']}), false)
  assert.equal(suiteVersionConfirmationRequired({gaps: ['suite_version_confirmation_required']}), true)
  assert.equal(suiteVersionConfirmationRequired({gaps: ['checker_suite_unavailable']}), false)
})

test('a missing personal model is reported apart from a failed file recognition', () => {
  assert.equal(personalModelRequired({gaps: ['personal_model_required']}), true)
  assert.equal(personalModelRequired({gaps: ['metadata_extraction_failed']}), false)
})

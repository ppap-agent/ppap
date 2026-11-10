import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi.ts'
import {createUploadPreparation, describeUploads, restoreUploads, type UploadPreparation, type PreparationAdapter} from './upload-preparation.ts'
import {canResumeUpload, canRetryUpload, type UploadRow} from './uploads.ts'
import {workbenchItems} from './workbench-items.ts'

const value: UploadPreparation = {version: 1, analysis_mode: 'standard', customer_preference: null, predecessor_id: null, uploads: [], files: []}
const draft = (revision: number | null, input = value, taskId: string | null = null) => ({id: 'original-draft', task_id: taskId, status: 'open', items: [], upload_preparation: input, preparation_revision: revision, analysis_mode: 'standard', predecessor_id: null, case_context: {}, writeback: {available: false}, updated_at: '2026-10-22'} as components['schemas']['Draft'])
const noFile = async () => {throw new Error('unexpected_file_read')}

test('preparation saves are ordered by acknowledged server revision', async () => {
  const calls: (number | null)[] = [], saved = [draft(0), draft(1, {...value, customer_preference: '客户'})]
  const controller = createUploadPreparation({readFile: noFile, read: async () => {throw new Error('unexpected_read')}, save: async (_id, revision) => {calls.push(revision); return saved.shift()!}})
  await Promise.all([controller.save('original-draft', value), controller.save('original-draft', {...value, customer_preference: '客户'})])
  assert.deepEqual(calls, [null, 0])
  assert.equal(controller.peek('original-draft')?.preparation_revision, 1)
})

test('a pending preparation does not block or release another draft', async () => {
  let started!: () => void, finish!: (value: ReturnType<typeof draft>) => void
  const firstStarted = new Promise<void>(resolve => {started = resolve})
  const firstReply = new Promise<ReturnType<typeof draft>>(resolve => {finish = resolve})
  const calls: string[] = []
  const controller = createUploadPreparation({readFile: noFile, read: async () => {throw new Error('unexpected_read')}, save: async (id, _revision, input) => {
    calls.push(id)
    if (id === 'draft-a') {started(); return firstReply}
    return {...draft(0, input), id}
  }})
  const first = controller.save('draft-a', value)
  await firstStarted
  const second = await controller.save('draft-b', {...value, customer_preference: '客户 B'})
  assert.equal(second.id, 'draft-b')
  assert.equal(controller.peek('draft-a'), undefined)
  finish({...draft(0), id: 'draft-a'})
  await first
  assert.deepEqual(calls, ['draft-a', 'draft-b'])
  assert.equal(controller.peek('draft-b')?.upload_preparation?.customer_preference, '客户 B')
})

test('a lost preparation write reply reads the same draft without repeating the write', async () => {
  const calls: string[] = []
  const controller = createUploadPreparation({readFile: noFile, save: async id => {calls.push(`write:${id}`); throw new Error('reply_lost')}, read: async id => {calls.push(`read:${id}`); return draft(0)}})
  assert.equal((await controller.save('original-draft', value)).preparation_revision, 0)
  assert.deepEqual(calls, ['write:original-draft', 'read:original-draft'])
})

test('an ambiguous or conflicting write blocks later writes until explicit readback', async () => {
  const calls: string[] = []
  const other = {...value, customer_preference: '其他页面'}
  const controller = createUploadPreparation({readFile: noFile, save: async () => {calls.push('write'); throw new Error('reply_lost')}, read: async () => {calls.push('read'); return draft(1, other)}})
  await assert.rejects(controller.save('original-draft', value))
  await assert.rejects(controller.save('original-draft', value))
  assert.deepEqual(calls, ['write', 'read'])
  assert.equal((await controller.read('original-draft')).upload_preparation?.customer_preference, '其他页面')
})

test('a promoted draft is read as its existing task and cannot be saved again', async () => {
  const controller = createUploadPreparation({readFile: noFile, read: async () => draft(null, value, 'original-draft'), save: async () => {throw new Error('unexpected_write')}})
  assert.equal((await controller.read('original-draft')).task_id, 'original-draft')
  await assert.rejects(controller.save('original-draft', value), /已开始识别/)
})

test('restored upload intent never pretends to retain local bytes or a successful receipt', () => {
  const row: UploadRow = {id: 'row', input: {kind: 'local', file: new File(['abc'], '同名.xlsx'), sha256: 'a'.repeat(64), destination: {token: 'folder', name: '选定目录'}}, receipt: {request_id: 'original-request', state: 'ready', file: null, retry_allowed: false, reason: null}, busy: false, removed: true}
  const described = describeUploads([row])
  assert.equal(JSON.stringify(described).includes('abc'), false)
  const restored = restoreUploads(described)[0]
  assert.equal(restored.receipt.request_id, 'original-request')
  assert.equal(restored.receipt.state, 'unknown')
  assert.equal(restored.removed, true)
  assert.equal(restored.input.file instanceof File, false)
  assert.equal(canRetryUpload({...restored, removed: false, receipt: {...restored.receipt, state: 'failed', retry_allowed: true}}), false)
  assert.equal(canResumeUpload({...restored, removed: false, receipt: {...restored.receipt, state: 'uploading', resume_allowed: true}}), false)
})

test('preparation drafts reopen in the upload flow; drafts of the retired pages are not listed', () => {
  const prepared = draft(0, {...value, uploads: [{request_id: 'pending', name: '待核对.xlsx', kind: 'feishu', removed: false, drive_file: {token: 'source', name: '待核对.xlsx', type: 'file'}}]})
  const items = workbenchItems([], [prepared, {...prepared, id: 'legacy', upload_preparation: null}])
  assert.equal(items[0].href, '/tasks/new?uploadDraft=original-draft')
  assert.deepEqual(items[0].files?.map(row => row.name), ['待核对.xlsx'])
  assert.equal(items.length, 1)
  assert.equal(workbenchItems([], [{...prepared, task_id: prepared.id}]).length, 0)
})

test('foreign draft receipts cannot replace the requested preparation', async () => {
  const adapter: PreparationAdapter = {readFile: noFile, read: async () => ({...draft(0), id: 'other-draft'}), save: async () => {throw new Error('unexpected_write')}}
  await assert.rejects(createUploadPreparation(adapter).read('original-draft'), /上传准备结果不匹配/)
})

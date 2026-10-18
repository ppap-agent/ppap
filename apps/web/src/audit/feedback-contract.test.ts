import assert from 'node:assert/strict'
import test from 'node:test'
import {readFeedbackAttachment, validFeedbackReceipt, validFeedbackHistory, type AuditFeedbackAdapter, type FeedbackFile, type FeedbackReceipt, type FeedbackHistory} from './feedback-contract.ts'

test('feedback history preserves the original request, ordered complete files and current access', () => {
  const file: FeedbackFile = {id: 'v1', file_id: 'f1', original_filename: '原附件.zip', status: 'ready', sha256: 'a'.repeat(64), content_type: 'application/zip', byte_size: 3}
  const receipt: FeedbackReceipt = {request: {request_id: 'r1', file_version_ids: ['v1']}, attempt_id: 'a1', state: 'unknown', submission_id: null, received_file_version_ids: [], received_at: null, retry_allowed: false, reason: null}
  const history: FeedbackHistory = {page: 1, page_size: 10, total: 1, entries: [{receipt, files: [file], files_available: true, created_at: '2026-10-18T00:00:00Z', reason: null}]}
  assert.equal(validFeedbackHistory(history, 1), true)
  for (const change of [{page: 2}, {total: 0}, {entries: [history.entries[0], history.entries[0]]}, {entries: [{...history.entries[0], files: []}]}, {entries: [{...history.entries[0], files: [{...file, id: 'other'}]}]}, {entries: [{...history.entries[0], files_available: false}]}]) assert.equal(validFeedbackHistory({...history, ...change}, 1), false)
  assert.equal(validFeedbackHistory({...history, entries: [{...history.entries[0], files: [], files_available: false, reason: '访问已受限'}]}, 1), true)
})

test('received feedback needs a nonempty distinct fixed collection and complete matching receipt', () => {
  const request = {request_id: 'request-1', file_version_ids: ['v1', 'v2']}
  const receipt: FeedbackReceipt = {request, attempt_id: 'attempt-1', state: 'received', submission_id: 'record-1', received_file_version_ids: ['v2', 'v1'], received_at: '2026-10-16T00:00:00Z', retry_allowed: false, reason: null}
  assert.equal(validFeedbackReceipt(receipt, request, 'attempt-1'), true)
  for (const change of [{attempt_id: 'old-attempt'}, {request: {...request, request_id: 'old-request'}}, {request: {...request, file_version_ids: ['v2', 'v1']}}, {received_file_version_ids: ['v1']}, {received_file_version_ids: ['v1', 'v1']}, {submission_id: null}, {received_at: 'not-a-date'}, {retry_allowed: true}]) assert.equal(validFeedbackReceipt({...receipt, ...change}, request, 'attempt-1'), false)
  const empty = {...request, file_version_ids: []}
  assert.equal(validFeedbackReceipt({...receipt, request: empty, received_file_version_ids: []}, empty, 'attempt-1'), false)
})

test('feedback attachments require a complete version before reading any bytes', async () => {
  let reads = 0
  const file: FeedbackFile = {id: 'v1', file_id: 'file-1', original_filename: 'sample.zip', content_type: 'application/zip', byte_size: 3, status: 'staging', sha256: null}
  const adapter = {readAttachment: async () => {reads += 1; return {file_version_id: 'v1', blob: new Blob(['abc'], {type: file.content_type})}}} as AuditFeedbackAdapter
  await assert.rejects(readFeedbackAttachment(adapter, file))
  assert.equal(reads, 0)
})

test('feedback attachment reads bind version, byte size and content type', async () => {
  const file: FeedbackFile = {id: 'v1', file_id: 'file-1', original_filename: 'sample.zip', content_type: 'application/zip', byte_size: 3, status: 'ready', sha256: null}
  const content = {file_version_id: 'v1', blob: new Blob(['abc'], {type: file.content_type})}
  const adapter = {readAttachment: async () => content} as AuditFeedbackAdapter
  assert.equal((await readFeedbackAttachment(adapter, file)).size, 3)
  for (const change of [{file_version_id: 'other-version'}, {blob: new Blob(['ab'], {type: file.content_type})}, {blob: new Blob(['abc'], {type: 'text/html'})}]) await assert.rejects(readFeedbackAttachment({...adapter, readAttachment: async () => ({...content, ...change})}, file))
})

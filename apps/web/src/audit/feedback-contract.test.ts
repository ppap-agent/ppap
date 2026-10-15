import assert from 'node:assert/strict'
import test from 'node:test'
import {readFeedbackAttachment, validFeedbackReceipt, type AuditFeedbackAdapter, type FeedbackFile, type FeedbackReceipt} from './feedback-contract.ts'

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

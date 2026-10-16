import assert from 'node:assert/strict'
import test from 'node:test'
import {canResumeUpload, canRetryUpload, type UploadRow} from './uploads.ts'

test('only an explicit terminal failure with retry authority permits another upload', () => {
  const row: UploadRow = {id: 'row', input: {kind: 'local', file: new File(['x'], 'a.pdf')}, removed: false, busy: false, receipt: {request_id: 'request', state: 'failed', file: null, reason: 'terminal; no write in flight', retry_allowed: true}}
  assert.equal(canRetryUpload(row), true)
  assert.equal(canRetryUpload({...row, receipt: {...row.receipt, state: 'unknown'}}), false)
  assert.equal(canRetryUpload({...row, receipt: {...row.receipt, state: 'uploading'}}), false)
  assert.equal(canRetryUpload({...row, receipt: {...row.receipt, retry_allowed: false}}), false)
  assert.equal(canRetryUpload({...row, busy: true}), false)
  assert.equal(canRetryUpload({...row, removed: true}), false)
})

test('only a server-authorized local or Drive transfer can continue the same request', () => {
  const row: UploadRow = {id: 'row', input: {kind: 'local', file: new File(['x'], 'a.pdf')}, removed: false, busy: false, receipt: {request_id: 'request', state: 'uploading', file: null, reason: '原件尚未接收', retry_allowed: false, resume_allowed: true}}
  assert.equal(canResumeUpload(row), true)
  assert.equal(canResumeUpload({...row, receipt: {...row.receipt, state: 'unknown'}}), false)
  assert.equal(canResumeUpload({...row, receipt: {...row.receipt, state: 'failed'}}), false)
  assert.equal(canResumeUpload({...row, receipt: {...row.receipt, state: 'ready'}}), false)
  assert.equal(canResumeUpload({...row, receipt: {...row.receipt, resume_allowed: false}}), false)
  assert.equal(canResumeUpload({...row, receipt: {...row.receipt, resume_allowed: undefined}}), false)
  assert.equal(canResumeUpload({...row, busy: true}), false)
  assert.equal(canResumeUpload({...row, removed: true}), false)
  assert.equal(canResumeUpload({...row, input: {kind: 'local', file: undefined!}}), false)
  const drive: UploadRow = {...row, input: {kind: 'feishu', file: {token: 'token', name: 'a.xlsx', type: 'file', size: 1}}}
  assert.equal(canResumeUpload(drive), true)
  assert.equal(canResumeUpload({...drive, receipt: {...drive.receipt, resume_allowed: undefined}}), false)
  assert.equal(canResumeUpload({...drive, receipt: {...drive.receipt, state: 'unknown'}}), false)
  assert.equal(canResumeUpload({...drive, busy: true}), false)
  assert.equal(canResumeUpload({...drive, removed: true}), false)
})

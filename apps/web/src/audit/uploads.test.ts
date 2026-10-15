import assert from 'node:assert/strict'
import test from 'node:test'
import {canRetryUpload, type UploadRow} from './uploads.ts'

test('only an explicit terminal failure with retry authority permits another upload', () => {
  const row: UploadRow = {id: 'row', input: {kind: 'local', file: new File(['x'], 'a.pdf')}, removed: false, busy: false, receipt: {request_id: 'request', state: 'failed', file: null, reason: 'terminal; no write in flight', retry_allowed: true}}
  assert.equal(canRetryUpload(row), true)
  assert.equal(canRetryUpload({...row, receipt: {...row.receipt, state: 'unknown'}}), false)
  assert.equal(canRetryUpload({...row, receipt: {...row.receipt, state: 'uploading'}}), false)
  assert.equal(canRetryUpload({...row, receipt: {...row.receipt, retry_allowed: false}}), false)
  assert.equal(canRetryUpload({...row, busy: true}), false)
  assert.equal(canRetryUpload({...row, removed: true}), false)
})

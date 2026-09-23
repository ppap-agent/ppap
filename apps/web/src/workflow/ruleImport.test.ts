import assert from 'node:assert/strict'
import test from 'node:test'
import {ruleImportReducer, type RuleImportState} from './ruleImport.ts'

const idle: RuleImportState<{title: string}> = {kind: 'idle', documentType: ''}

test('unknown parse is read back and is not resubmitted automatically', () => {
  const parsing = ruleImportReducer(idle, {type: 'begin-upload'})
  const reading = ruleImportReducer(parsing, {type: 'begin-parse', jobId: 'job-1'})
  const unknown = ruleImportReducer(reading, {type: 'observed', job: {id: 'job-1', status: 'unknown'}})
  assert.equal(unknown.kind, 'unknown')
  const reread = ruleImportReducer(unknown, {type: 'read-back'})
  assert.equal(reread.kind, 'parsing')
  if (reread.kind === 'parsing') assert.equal(reread.jobId, 'job-1')
  const rerun = ruleImportReducer(unknown, {type: 'begin-rerun'})
  assert.equal(rerun.kind, 'unknown')
})

test('explicit rerun requires a stored source', () => {
  const failed = ruleImportReducer(
    ruleImportReducer(ruleImportReducer(idle, {type: 'begin-upload'}), {type: 'begin-parse', jobId: 'job-1', source: {file_id: 'f', file_version_id: 'v', original_filename: 'a.json'}}),
    {type: 'observed', job: {id: 'job-1', status: 'failed', error: 'bad'}},
  )
  const rerun = ruleImportReducer(failed, {type: 'begin-rerun'})
  assert.equal(rerun.kind, 'uploading')
})

test('resume records source without starting another parse', () => {
  const parsing = ruleImportReducer(ruleImportReducer(idle, {type: 'begin-upload'}), {
    type: 'begin-parse',
    jobId: 'job-1',
  })
  const noted = ruleImportReducer(parsing, {
    type: 'record-source',
    documentType: '控制计划',
    source: {file_id: 'f', file_version_id: 'v', original_filename: '规则原件'},
  })
  assert.equal(noted.kind, 'parsing')
  if (noted.kind === 'parsing') {
    assert.equal(noted.jobId, 'job-1')
    assert.equal(noted.documentType, '控制计划')
    assert.equal(noted.source?.file_id, 'f')
  }
  assert.equal(ruleImportReducer(noted, {type: 'begin-parse', jobId: 'job-2'}).kind, 'parsing')
  const queued = ruleImportReducer(noted, {type: 'observed', job: {id: 'job-1', status: 'queued'}})
  assert.equal(queued.kind, 'parsing')
  if (queued.kind === 'parsing') assert.equal(queued.progress, 'queued')
})

test('catalog failure does not replace an in-flight parse', () => {
  const parsing = ruleImportReducer(ruleImportReducer(idle, {type: 'begin-upload'}), {type: 'begin-parse', jobId: 'job-1'})
  assert.equal(ruleImportReducer(parsing, {type: 'catalog-failed', message: '无法读取规则类型'}).kind, 'parsing')
  const failed = ruleImportReducer(idle, {type: 'catalog-failed', message: '无法读取规则类型'})
  assert.equal(failed.kind, 'failed')
  if (failed.kind === 'failed') assert.equal(failed.jobId, undefined)
})

test('publish failure returns to the same candidate', () => {
  const reviewing = ruleImportReducer(
    ruleImportReducer(ruleImportReducer(idle, {type: 'begin-upload'}), {type: 'begin-parse', jobId: 'job-1'}),
    {type: 'observed', job: {id: 'job-1', status: 'succeeded', result: {title: '规则'}}},
  )
  const publishing = ruleImportReducer(reviewing, {type: 'begin-publish', leaveAfter: true})
  const again = ruleImportReducer(publishing, {type: 'publish-failed', message: '保存规则失败'})
  assert.equal(again.kind, 'reviewing')
  if (again.kind === 'reviewing') {
    assert.equal(again.candidate.title, '规则')
    assert.equal(again.leavePrompt, true)
    assert.equal(again.message, '保存规则失败')
  }
})

test('publish cannot start from idle or while already publishing', () => {
  assert.equal(ruleImportReducer(idle, {type: 'begin-publish', leaveAfter: false}).kind, 'idle')
  const reviewing = ruleImportReducer(
    ruleImportReducer(ruleImportReducer(idle, {type: 'begin-upload'}), {type: 'begin-parse', jobId: 'job-1'}),
    {type: 'observed', job: {id: 'job-1', status: 'succeeded', result: {title: '规则'}}},
  )
  const publishing = ruleImportReducer(reviewing, {type: 'begin-publish', leaveAfter: false})
  assert.equal(publishing.kind, 'publishing')
  assert.equal(ruleImportReducer(publishing, {type: 'begin-publish', leaveAfter: true}).kind, 'publishing')
})

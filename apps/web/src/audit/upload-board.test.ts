import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi'
import {listedTypes, matchStatus, recognitionHold, sourceMismatches, uploadFileStatus} from './upload-board.ts'

type File = components['schemas']['AuditTaskFile']
type Match = NonNullable<File['match']>
const file = (id: string, extra: Partial<File> = {}): File => ({file: {id, file_id: `f-${id}`, original_filename: `${id}.xlsx`, byte_size: 1, content_type: 'application/octet-stream', sha256: null, status: 'ready', created_at: ''}, selection: 'selected', removal_reason: null, validation: {state: 'valid', reasons: []}, match: null, ...extra}) as File
const match = (state: Match['state'], extra: Partial<Match> = {}) => ({state, document_type: null, metadata: {}, gaps: [], rule: null, ...extra}) as Match

test('the upload page states only whether a file is in place, never its recognition result', () => {
  assert.equal(uploadFileStatus(file('a')), null)
  assert.equal(uploadFileStatus(file('a', {match: match('matched', {rule: {id: 'r'} as Match['rule']})})), null)
  assert.equal(uploadFileStatus(file('a', {match: match('unmatched')})), null)
  assert.equal(uploadFileStatus(file('a', {validation: {state: 'checking', reasons: []}}))?.label, '检查中')
  assert.equal(uploadFileStatus(file('a', {validation: {state: 'invalid', reasons: ['空文件']}}))?.label, '无法审核')
  assert.equal(uploadFileStatus(file('a', {reference_only: true}))?.label, '比对资料')
  assert.equal(uploadFileStatus(file('a', {selection: 'unselected', reference_only: true})), null)
})

test('the confirm-rules page names each recognition result; a file without one is "识别中" only while recognition runs', () => {
  assert.equal(matchStatus(null, true).label, '识别中')
  assert.equal(matchStatus(match('matching'), false).label, '识别中')
  assert.equal(matchStatus(match('pending'), false).label, '等待识别')
  assert.equal(matchStatus(match('matched', {rule: {id: 'r'} as Match['rule']}), false).label, '已匹配')
  assert.equal(matchStatus(match('unmatched'), false).label, '未匹配')
  assert.equal(matchStatus(match('reference'), false).label, '比对资料')
  assert.equal(matchStatus(match('failed', {gaps: ['checker_suite_unavailable']}), false).label, '暂不能审核')
})

test('the reason recognition cannot start is the first thing the person must deal with', () => {
  const ready = {recognizing: false, saving: false, preparationPending: false, needsCurrent: false, customer: true, transferBlocked: false, checking: 0, exceptions: 0, selected: 1}
  assert.equal(recognitionHold(ready), '')
  assert.equal(recognitionHold({...ready, recognizing: true, checking: 2}), '正在识别，完成后可修改。')
  assert.equal(recognitionHold({...ready, saving: true, checking: 1}), '正在保存本次文件…')
  assert.equal(recognitionHold({...ready, preparationPending: true}), '上传准备待确认，请先读取准备记录。')
  assert.equal(recognitionHold({...ready, uploading: 3, checking: 1}), '正在上传…（还有 3 份）')
  assert.equal(recognitionHold({...ready, transferBlocked: true, uploading: 1}), '请先处理或移除未完成的传输，再开始识别。')
  assert.equal(recognitionHold({...ready, needsCurrent: true}), '任务已有更新，请先读取当前任务。')
  assert.equal(recognitionHold({...ready, customer: false, checking: 1}), '请选择审核客户后再开始识别。')
  assert.equal(recognitionHold({...ready, transferBlocked: true, checking: 2}), '请先处理或移除未完成的传输，再开始识别。')
  assert.equal(recognitionHold({...ready, checking: 2, exceptions: 1}), '正在检查文件…（还有 2 份）')
  assert.equal(recognitionHold({...ready, exceptions: 1}), '请先处理或移除异常文件。')
  assert.equal(recognitionHold({...ready, selected: 0}), '至少选择一份可审核文件。')
})

test('the customer types toggle names how many more types there are, not the total', () => {
  const many = Array.from({length: 19}, (_, index) => ({type: `T${index}`, label: `类型 ${index}`}))
  const closed = listedTypes(many, false)
  assert.equal(closed.shown.length, 6)
  assert.equal(closed.toggle, '还有 13 类')
  assert.deepEqual(listedTypes(many, true), {shown: many, toggle: '收起'})
  assert.deepEqual(listedTypes(many.slice(0, 6), false), {shown: many.slice(0, 6), toggle: ''})
})

test('only an S07 status of the file shows the source-mismatch prompt; a missing counterpart (S04) never does', () => {
  const fact = {document: '控制计划', document_type: 'E12', identity: '零件号', value: 'P-204', found_value: 'P-999', found_file_version_id: 'peer', found_filename: '控制计划.xlsx'}
  const statuses = [
    {code: 'S07', scope: 'content', file_version_id: 'target', filename: 'PFMEA.xlsx', note: '', source_mismatch: fact},
    {code: 'S04', scope: 'content', file_version_id: 'target', filename: 'PFMEA.xlsx', note: '', missing: {document: '控制计划', document_type: 'E12'}},
    {code: 'S07', scope: 'content', file_version_id: 'other', filename: '其他.xlsx', note: '', source_mismatch: {...fact, found_file_version_id: 'peer-2'}},
  ]
  assert.deepEqual(sourceMismatches(statuses, 'target'), [fact])
  assert.deepEqual(sourceMismatches(statuses.filter(row => row.code === 'S04'), 'target'), [])
  // An S07 without its fact cannot name either side, so it shows no prompt rather than a guessed one.
  assert.deepEqual(sourceMismatches([{code: 'S07', file_version_id: 'target', filename: 'PFMEA.xlsx', note: ''}], 'target'), [])
  assert.deepEqual(sourceMismatches(undefined, 'target'), [])
})

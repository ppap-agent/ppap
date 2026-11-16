import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi'
import {missingTypes, recognitionHold, uploadBoard, uploadFileStatus, uploadStatusText} from './upload-board.ts'

type File = components['schemas']['AuditTaskFile']
type Match = NonNullable<File['match']>
const file = (id: string, extra: Partial<File> = {}): File => ({file: {id, file_id: `f-${id}`, original_filename: `${id}.xlsx`, byte_size: 1, content_type: 'application/octet-stream', sha256: null, status: 'ready', created_at: ''}, selection: 'selected', removal_reason: null, validation: {state: 'valid', reasons: []}, match: null, ...extra}) as File
const match = (state: Match['state'], extra: Partial<Match> = {}) => ({state, document_type: null, metadata: {}, gaps: [], rule: null, ...extra}) as Match
const slots = [{type: '控制计划', label: '控制计划'}, {type: '过程流程图', label: '过程流程图'}, {type: 'E07', label: '生产件样品照片'}]

test('a file not yet recognized waits for the person; it is "识别中" only while recognition runs', () => {
  assert.equal(uploadFileStatus(file('a'), false).label, '待识别')
  assert.equal(uploadFileStatus(file('a', {match: match('pending')}), false).label, '待识别')
  assert.equal(uploadFileStatus(file('a'), true).label, '识别中')
  assert.equal(uploadFileStatus(file('a', {match: match('matching')}), true).label, '识别中')
  assert.equal(uploadFileStatus(file('a', {validation: {state: 'checking', reasons: []}}), true).label, '检查中')
  assert.equal(uploadFileStatus(file('a', {validation: {state: 'invalid', reasons: ['空文件']}}), false).label, '无法审核')
  assert.equal(uploadFileStatus(file('a', {selection: 'unselected'}), false).label, '未选择')
  assert.equal(uploadFileStatus(file('a', {reference_only: true}), false).label, '比对资料')
  assert.equal(uploadFileStatus(file('a', {match: match('matched', {rule: {id: 'r'} as Match['rule']})}), false).label, '已匹配')
  assert.equal(uploadFileStatus(file('a', {match: match('unmatched')}), false).label, '未匹配')
  assert.equal(uploadFileStatus(file('a', {match: match('failed', {gaps: ['checker_suite_unavailable']})}), false).label, '暂不能审核')
})

test('the board opens only types that have files; files are "已添加" before recognition and "未识别出类型" after it', () => {
  const board = uploadBoard(slots, [
    file('cp', {match: match('matched', {document_type: '控制计划'})}),
    file('photo', {match: match('matched', {document_type: '生产件样品照片'})}),
    file('new'),
    file('waiting', {match: match('pending')}),
    file('none', {match: match('unmatched')}),
    file('other', {match: match('unmatched', {document_type: 'E01'})}),
    file('broken', {validation: {state: 'error', reasons: []}, match: match('matched', {document_type: '控制计划'})}),
  ])
  assert.deepEqual(board.cards.map(card => [card.slot.label, card.rows.map(row => row.file.id)]), [['控制计划', ['cp']], ['生产件样品照片', ['photo']], ['初始样品检验报告', ['other']]])
  assert.deepEqual(board.missing.map(slot => slot.label), ['过程流程图'])
  assert.deepEqual(board.added.map(row => row.file.id), ['new', 'waiting'])
  assert.deepEqual(board.untyped.map(row => row.file.id), ['none'])
  assert.deepEqual(board.exceptions.map(row => row.file.id), ['broken'])
  // Automatic mode has no customer types; recognized types still get their own card.
  const auto = uploadBoard(null, [file('cp', {match: match('matched', {document_type: '控制计划'})}), file('cp2', {match: match('matched', {document_type: '控制计划'})})])
  assert.deepEqual(auto.cards.map(card => [card.slot.type, card.rows.length]), [['控制计划', 2]])
  assert.deepEqual(auto.missing, [])
})

test('the reason recognition cannot start is the first thing the person must deal with', () => {
  const ready = {recognizing: false, saving: false, preparationPending: false, needsCurrent: false, transferBlocked: false, checking: 0, exceptions: 0, selected: 1}
  assert.equal(recognitionHold(ready), '')
  assert.equal(recognitionHold({...ready, recognizing: true, checking: 2}), '正在识别，完成后可修改。')
  assert.equal(recognitionHold({...ready, saving: true, checking: 1}), '正在保存本次文件…')
  assert.equal(recognitionHold({...ready, preparationPending: true}), '上传准备待确认，请先读取准备记录。')
  assert.equal(recognitionHold({...ready, uploading: 3, checking: 1}), '正在上传…（还有 3 份）')
  assert.equal(recognitionHold({...ready, transferBlocked: true, uploading: 1}), '请先处理或排除未完成的传输，再开始识别。')
  assert.equal(recognitionHold({...ready, needsCurrent: true}), '任务已有更新，请先读取当前任务。')
  assert.equal(recognitionHold({...ready, transferBlocked: true, checking: 2}), '请先处理或排除未完成的传输，再开始识别。')
  assert.equal(recognitionHold({...ready, checking: 2, exceptions: 1}), '正在检查文件…（还有 2 份）')
  assert.equal(recognitionHold({...ready, exceptions: 1}), '请先处理或移除异常文件。')
  assert.equal(recognitionHold({...ready, selected: 0}), '至少选择一份可审核文件。')
})

test('a failed read while recognition runs gives its reason instead of the wait note', () => {
  const base = {recognizing: true, readError: '', hasWork: true, canEnd: false, canOpenRules: false, canStart: false, held: '正在识别，完成后可修改。'}
  assert.equal(uploadStatusText(base), '正在识别，完成后可修改。')
  const failed = '任务结果读取失败，任务与文件仍保留。重新读取不会重新创建任务或开始审核。'
  assert.equal(uploadStatusText({...base, readError: failed}), failed)
  assert.equal(uploadStatusText({...base, recognizing: false, readError: failed, canOpenRules: true}), failed)
  assert.equal(uploadStatusText({...base, recognizing: false, held: '', canOpenRules: true}), '识别完成，请确认每份文件的审核规则。')
  assert.equal(uploadStatusText({...base, recognizing: false, held: '', canStart: true}), '识别不消耗点数；确认规则后才开始计费审核。')
  assert.equal(uploadStatusText({...base, recognizing: false, hasWork: false}), '')
})

test('the missing-types toggle names how many more types there are, not the total', () => {
  const many = Array.from({length: 19}, (_, index) => ({type: `T${index}`, label: `类型 ${index}`}))
  const closed = missingTypes(many, false)
  assert.equal(closed.shown.length, 6)
  assert.equal(closed.toggle, '还有 13 类')
  assert.deepEqual(missingTypes(many, true), {shown: many, toggle: '收起'})
  assert.deepEqual(missingTypes(many.slice(0, 6), false), {shown: many.slice(0, 6), toggle: ''})
})

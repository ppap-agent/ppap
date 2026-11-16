import type {components} from '../generated/openapi'
import {slotForDocumentType, type FileSlot} from './customer-coverage.ts'
import {documentTypeLabel} from './status-labels.ts'
import {checkerSuiteUnavailable} from './task-acceptance.ts'

type File = components['schemas']['AuditTaskFile']
export type UploadFileStatus = {label: string; tone: 'success' | 'warning' | 'destructive' | 'outline'}

/** One file's state on the upload board. Without a recognition in progress, a file not yet recognized waits for the person to start it. */
export function uploadFileStatus(row: File, recognizing: boolean): UploadFileStatus {
  if (row.validation.state === 'invalid' || row.validation.state === 'error') return {label: '无法审核', tone: 'destructive'}
  if (row.validation.state === 'pending' || row.validation.state === 'checking') return {label: '检查中', tone: 'outline'}
  if (row.selection !== 'selected') return {label: '未选择', tone: 'outline'}
  if (row.reference_only || row.match?.state === 'reference') return {label: '比对资料', tone: 'outline'}
  return matchStatus(row.match, recognizing)
}

/** A file's recognition result in one vocabulary, on the upload board and the confirm-rules page alike. */
export function matchStatus(match: File['match'], recognizing: boolean): UploadFileStatus {
  if (match?.state === 'matched' && match.rule) return {label: '已匹配', tone: 'success'}
  if (match?.state === 'reference') return {label: '比对资料', tone: 'outline'}
  if (match?.state === 'unmatched') return {label: '未匹配', tone: 'warning'}
  if (!match || match.state === 'pending' || match.state === 'matching') return recognizing || match?.state === 'matching' ? {label: '识别中', tone: 'outline'} : {label: '待识别', tone: 'outline'}
  if (checkerSuiteUnavailable(match)) return {label: '暂不能审核', tone: 'warning'}
  return {label: '识别异常', tone: 'warning'}
}

const recognized = (row: File) => Boolean(row.match && row.match.state !== 'pending' && row.match.state !== 'matching')

type UploadBoard = {
  /** Types that have files, in the customer's order, then types recognition found outside that list. */
  cards: {slot: FileSlot; rows: File[]}[]
  /** The customer's types without a file yet. */
  missing: FileSlot[]
  /** Not recognized yet. */
  added: File[]
  /** Recognized without a document type. */
  untyped: File[]
  /** Files that cannot be audited; they are removed together. */
  exceptions: File[]
}

/** Places each file shown on the upload board: a recognized type joins its card; nothing is placed by guess. */
export function uploadBoard(slots: FileSlot[] | null, rows: File[]): UploadBoard {
  const board: UploadBoard = {cards: (slots ?? []).map(slot => ({slot, rows: []})), missing: [], added: [], untyped: [], exceptions: []}
  for (const row of rows) {
    if (row.validation.state === 'invalid' || row.validation.state === 'error') {board.exceptions.push(row); continue}
    const type = row.match?.document_type?.trim()
    if (!type) {(recognized(row) ? board.untyped : board.added).push(row); continue}
    const slot = slotForDocumentType(slots, type)
    const card = slot ? board.cards.find(item => item.slot.type === slot.type) : board.cards.find(item => item.slot.type === type)
    if (card) card.rows.push(row)
    else board.cards.push({slot: {type, label: documentTypeLabel(type)}, rows: [row]})
  }
  board.missing = board.cards.filter(card => !card.rows.length).map(card => card.slot)
  board.cards = board.cards.filter(card => card.rows.length)
  return board
}

const missingLimit = 6
/** The "还缺" line: the first few missing types, and its toggle names how many more there are (empty when all fit). */
export function missingTypes(missing: FileSlot[], open: boolean): {shown: FileSlot[]; toggle: string} {
  if (missing.length <= missingLimit) return {shown: missing, toggle: ''}
  return open ? {shown: missing, toggle: '收起'} : {shown: missing.slice(0, missingLimit), toggle: `还有 ${missing.length - missingLimit} 类`}
}

/** The upload board's status line. A failed read of the task comes first, also while recognition runs, in the confirm-rules page's words. */
export function uploadStatusText(input: {recognizing: boolean; readError: string; hasWork: boolean; canEnd: boolean; canOpenRules: boolean; canStart: boolean; held: string}): string {
  if (input.recognizing) return input.readError || input.held
  if (!input.hasWork) return ''
  if (input.readError) return input.readError
  if (input.canEnd) return '没有待审目标，可以结束任务，不收费、不生成报告。'
  if (input.canOpenRules) return '识别完成，请确认每份文件的审核规则。'
  return input.held || (input.canStart ? '识别不消耗点数；确认规则后才开始计费审核。' : '')
}

/** Why recognition cannot start now, in the order the person would deal with it. Empty when it can start. */
export function recognitionHold(input: {recognizing: boolean; saving: boolean; preparationPending: boolean; needsCurrent: boolean; transferBlocked: boolean; uploading?: number; checking: number; exceptions: number; selected: number}): string {
  if (input.recognizing) return '正在识别，完成后可修改。'
  if (input.saving) return '正在保存本次文件…'
  if (input.preparationPending) return '上传准备待确认，请先读取准备记录。'
  if (input.needsCurrent) return '任务已有更新，请先读取当前任务。'
  if (input.transferBlocked) return '请先处理或排除未完成的传输，再开始识别。'
  if (input.uploading) return `正在上传…（还有 ${input.uploading} 份）`
  if (input.checking) return `正在检查文件…（还有 ${input.checking} 份）`
  if (input.exceptions) return '请先处理或移除异常文件。'
  if (!input.selected) return '至少选择一份可审核文件。'
  return ''
}

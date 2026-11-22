import type {components} from '../generated/openapi'
import type {FileSlot} from './customer-coverage.ts'
import {checkerSuiteUnavailable} from './task-acceptance.ts'

type File = components['schemas']['AuditTaskFile']
export type UploadFileStatus = {label: string; tone: 'success' | 'warning' | 'destructive' | 'outline'}

/** One file's state on the upload page: only whether it is in place and readable. Recognition results belong to the confirm-rules page. */
export function uploadFileStatus(row: File): UploadFileStatus | null {
  if (row.validation.state === 'invalid' || row.validation.state === 'error') return {label: '无法审核', tone: 'destructive'}
  if (row.validation.state === 'pending' || row.validation.state === 'checking') return {label: '检查中', tone: 'outline'}
  if (row.selection === 'selected' && row.reference_only) return {label: '比对资料', tone: 'outline'}
  return null
}

/** A file's recognition result on the confirm-rules page. A file without a result is still being recognized while recognition runs. */
export function matchStatus(match: File['match'], recognizing: boolean): UploadFileStatus {
  if (match?.state === 'matched' && match.rule) return {label: '已匹配', tone: 'success'}
  if (match?.state === 'reference') return {label: '比对资料', tone: 'outline'}
  if (match?.state === 'unmatched') return {label: '未匹配', tone: 'warning'}
  if (!match || match.state === 'pending' || match.state === 'matching') return {label: recognizing || match?.state === 'matching' ? '识别中' : '等待识别', tone: 'outline'}
  if (checkerSuiteUnavailable(match)) return {label: '暂不能审核', tone: 'warning'}
  return {label: '识别异常', tone: 'warning'}
}

const listedLimit = 6
/** The "该客户需要" hint: the first few of the customer's file types, and its toggle names how many more there are (empty when all fit). */
export function listedTypes(types: FileSlot[], open: boolean): {shown: FileSlot[]; toggle: string} {
  if (types.length <= listedLimit) return {shown: types, toggle: ''}
  return open ? {shown: types, toggle: '收起'} : {shown: types.slice(0, listedLimit), toggle: `还有 ${types.length - listedLimit} 类`}
}

/** Why recognition cannot start now, in the order the person would deal with it. Empty when it can start. */
export function recognitionHold(input: {recognizing: boolean; saving: boolean; preparationPending: boolean; needsCurrent: boolean; customer: boolean; transferBlocked: boolean; uploading?: number; checking: number; exceptions: number; selected: number}): string {
  if (input.recognizing) return '正在识别，完成后可修改。'
  if (input.saving) return '正在保存本次文件…'
  if (input.preparationPending) return '上传准备待确认，请先读取准备记录。'
  if (input.needsCurrent) return '任务已有更新，请先读取当前任务。'
  if (!input.customer) return '请选择审核客户后再开始识别。'
  if (input.transferBlocked) return '请先处理或移除未完成的传输，再开始识别。'
  if (input.uploading) return `正在上传…（还有 ${input.uploading} 份）`
  if (input.checking) return `正在检查文件…（还有 ${input.checking} 份）`
  if (input.exceptions) return '请先处理或移除异常文件。'
  if (!input.selected) return '至少选择一份可审核文件。'
  return ''
}

type PrecheckStatus = components['schemas']['PrecheckStatus']
/** S07 fact of the precheck: the associated counterpart names another part, drawing or project than the audited file. */
export type SourceMismatch = NonNullable<PrecheckStatus['source_mismatch']>
export function sourceMismatches(statuses: PrecheckStatus[] | undefined, fileVersionId: string): SourceMismatch[] {
  return (statuses ?? []).flatMap(status => {
    const fact = status.source_mismatch
    return status.code === 'S07' && status.file_version_id === fileVersionId && fact ? [fact] : []
  })
}

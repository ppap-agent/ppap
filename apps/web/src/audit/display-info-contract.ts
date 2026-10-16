import type {components} from '../generated/openapi'
import {sameSnapshot} from './result-projection.ts'

export type DisplayInfo = components['schemas']['AuditFileDisplayInfo']
export type DisplayValues = components['schemas']['AuditFileDisplayValues']
export type DisplayReference = components['schemas']['AuditFileDisplayReference']
export type DisplayRequest = components['schemas']['AuditFileDisplayRequest']
export type DisplayReceipt = components['schemas']['AuditFileDisplayReceipt']
type Task = components['schemas']['AuditTask']
type File = components['schemas']['AuditTaskFile']
export const displayFields = [['customer', '客户'], ['model', '车型'], ['part_number', '零件号'], ['part', '零件名称']] as const
export function validDisplayInfo(info: DisplayInfo | null | undefined): info is DisplayInfo {
  return Boolean(info && Number.isSafeInteger(info.revision) && info.revision >= 0 && info.values && Object.keys(info.values).length === 4 && displayFields.every(([key]) => info.values[key] === null || typeof info.values[key] === 'string'))
}
export function unconfirmedDisplayAccess(access: File['display_edit_access'], reason: string): NonNullable<File['display_edit_access']> {
  return access?.state === 'denied' || access?.state === 'deleted' ? access : {state: 'unknown', reason}
}

/** Display values do not regress; retained values cannot retain old authority. */
export function reconcileTaskDisplay(previous: Task | null | undefined, next: Task): Task {
  if (!previous?.round_id || previous.id !== next.id || previous.round_id !== next.round_id) return next
  return {...next, files: next.files.map(file => {
    const known = previous.files.find(row => row.file.id === file.file.id)?.display_info
    if (!validDisplayInfo(known)) return file
    const incoming = file.display_info
    const conflict = validDisplayInfo(incoming) && incoming.revision === known.revision && !sameSnapshot(incoming.values, known.values)
    if (validDisplayInfo(incoming) && incoming.revision >= known.revision && !conflict) return file
    return {...file, display_info: known, display_edit_access: unconfirmedDisplayAccess(file.display_edit_access, conflict ? '同一显示版本的数据不一致，请重新读取后再更正。' : '已保留最近确认的显示信息，编辑许可待重新读取。')}
  })}
}

export function fileDisplayValues(file: File): DisplayValues {
  if (validDisplayInfo(file.display_info)) return file.display_info.values
  const metadata = file.match?.metadata
  return {customer: metadata?.customer ?? null, model: metadata?.model ?? null, part_number: metadata?.part_number ?? null, part: metadata?.part ?? null}
}

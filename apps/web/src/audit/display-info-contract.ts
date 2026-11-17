import type {components} from '../generated/openapi'
import {sameSnapshot} from './result-projection.ts'

export type DisplayInfo = components['schemas']['AuditFileDisplayInfo']
export type DisplayValues = components['schemas']['AuditFileDisplayValues']
export type DisplayReference = components['schemas']['AuditFileDisplayReference']
export type DisplayRequest = components['schemas']['AuditFileDisplayRequest']
export type DisplayReceipt = components['schemas']['AuditFileDisplayReceipt']
type Task = components['schemas']['AuditTask']
type File = components['schemas']['AuditTaskFile']
export const displayFields = [['project', '项目名称'], ['customer', '客户'], ['model', '车型'], ['part_number', '零件号'], ['part', '零件名称']] as const
export function validDisplayInfo(info: DisplayInfo | null | undefined): info is DisplayInfo {
  return Boolean(info && Number.isSafeInteger(info.revision) && info.revision >= 0 && info.values && Object.keys(info.values).length === displayFields.length && displayFields.every(([key]) => info.values[key] === null || typeof info.values[key] === 'string'))
}
/** Display values do not regress. */
export function reconcileTaskDisplay(previous: Task | null | undefined, next: Task): Task {
  if (!previous?.round_id || previous.id !== next.id || previous.round_id !== next.round_id) return next
  return {...next, files: next.files.map(file => {
    const known = previous.files.find(row => row.file.id === file.file.id)?.display_info
    if (!validDisplayInfo(known)) return file
    const incoming = file.display_info
    const conflict = validDisplayInfo(incoming) && incoming.revision === known.revision && !sameSnapshot(incoming.values, known.values)
    if (validDisplayInfo(incoming) && incoming.revision >= known.revision && !conflict) return file
    return {...file, display_info: known}
  })}
}

export function fileDisplayValues(file: Partial<Pick<File, 'display_info' | 'match'>>): DisplayValues {
  if (validDisplayInfo(file.display_info)) return file.display_info.values
  const metadata = file.match?.metadata
  return {customer: metadata?.customer ?? null, model: metadata?.model ?? null, project: metadata?.project ?? null, part_number: metadata?.part_number ?? null, part: metadata?.part ?? null}
}

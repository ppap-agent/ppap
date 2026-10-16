import type {components} from '../generated/openapi'

export type ArchiveQuery = components['schemas']['AuditArchiveQuery']
export type ArchiveDocument = components['schemas']['AuditArchiveDocument']
export type HistoryEntry = components['schemas']['AuditHistoryEntry']
export type HistoryDetail = components['schemas']['AuditHistoryDetail']
export type ResultReference = components['schemas']['AuditResultReference']
export type AuditArchiveAdapter = {
  listDocuments: (query: ArchiveQuery) => Promise<components['schemas']['AuditArchivePage']>
  listHistory: (documentId: string) => Promise<components['schemas']['AuditHistoryPage']>
  readHistory: (documentId: string, reference: ResultReference) => Promise<HistoryDetail>
}
export const resultKey = (reference: ResultReference) => JSON.stringify([reference.task_id, reference.round_id, reference.file_version_id, reference.result_attempt])
export function readArchiveAddress(search: string) {
  const params = new URLSearchParams(search), documentId = params.get('archiveDoc'), raw = params.get('auditRef')
  let reference: ResultReference | null = null, invalid = params.has('auditRef') && !raw || params.get('archiveReport') === '1' && !raw
  if (raw) try {
    const values: unknown = JSON.parse(raw)
    if (!Array.isArray(values) || values.length !== 4 || !values.slice(0, 3).every(value => typeof value === 'string' && value.length > 0 && value.length <= 256) || values[3] !== null && (!Number.isInteger(values[3]) || values[3] < 1)) throw new Error('invalid_history_reference')
    reference = {task_id: values[0], round_id: values[1], file_version_id: values[2], result_attempt: values[3]}
  } catch {invalid = true}
  return {documentId, reference, invalid, report: params.get('archiveReport') === '1'}
}
export function writeArchiveAddress(documentId: string | null, reference: ResultReference | null, report = false, replace = false, view: 'archive' | 'usage' = 'archive') {
  const url = new URL(location.href)
  url.searchParams.set('view', view)
  for (const [key, value] of Object.entries({archiveDoc: documentId, auditRef: reference ? resultKey(reference) : null, archiveReport: report ? '1' : null})) {
    if (value) url.searchParams.set(key, value); else url.searchParams.delete(key)
  }
  history[replace ? 'replaceState' : 'pushState'](history.state, '', url)
  if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-view', view, archiveDoc: documentId, auditRef: reference ? resultKey(reference) : null, archiveReport: report ? '1' : null}, location.origin)
}
export function historyReportReference(entry: HistoryEntry): components['schemas']['AuditReportReference'] | null {
  return entry.reference.result_attempt !== null && entry.report.report_id ? {...entry.reference, result_attempt: entry.reference.result_attempt, report_id: entry.report.report_id} : null
}
export function historyShareMatches(entry: HistoryEntry, share?: components['schemas']['AuditReportShare'] | null): boolean {
  if (!share) return true
  const reference = historyReportReference(entry), scope = share.request.reports
  return Boolean(reference && scope.length === 1 && resultKey(scope[0]) === resultKey(reference) && scope[0].report_id === reference.report_id)
}
export function historyReportReady(entry: HistoryEntry): boolean {
  const report = entry.report
  return Boolean(historyReportReference(entry) && entry.execution === 'completed' && ['PASS', 'NG'].includes(entry.conclusion || '') && entry.report_access.state === 'allowed' && report.state === 'ready' && report.generated_at && report.artifact?.artifact_id && report.artifact.content_type === 'application/pdf' && report.artifact.byte_size > 0)
}

import type {components} from '../src/generated/openapi'
import type {ArchiveDocument, AuditArchiveAdapter, HistoryDetail, HistoryEntry} from '../src/audit/archive-contract'
import {resultKey, historyReportReference} from '../src/audit/archive-contract'
import type {AuditReportReference} from '../src/audit/report-contract'
import type {ReportShare} from '../src/audit/sharing-contract'
import {fixtureReport, withReportBytes} from './audit-reports'

type Task = components['schemas']['AuditTask']
const allowed = {state: 'allowed' as const, reason: null}
const denied = {state: 'denied' as const, reason: '无权查看来源任务。'}

/** Provider-confirmed sample identities only. Arbitrary uploads are not archived. */
export function fixtureArchive(tasks: Map<string, Task>, scene: string, seed: (id: string, sequence: number, failed: boolean, pending: boolean) => Task, readShare: (reference: AuditReportReference) => ReportShare | null): AuditArchiveAdapter {
  const documents: ArchiveDocument[] = []
  const entries = new Map<string, HistoryEntry[]>()
  const details = new Map<string, HistoryDetail>()
  let initializing: Promise<void> | undefined
  let historyListFailed = false
  async function initialize() {
    if (!initializing) initializing = (async () => {
      for (let index = 0; index < 12; index += 1) {
        const short = `A${String(index + 1).padStart(3, '0')}`, documentId = `preview-archive-${short}`
        const metadata = {customer: index === 1 ? '示例客户 B' : '示例客户 A', model: index === 3 ? null : index < 3 ? 'CETUS' : '其他示例车型', part: index === 3 ? null : index === 1 ? '前翼子板' : '尾门导流板'}
        const history: HistoryEntry[] = []
        for (const sequence of index === 0 ? [4, 3, 2, 1] : [1]) {
          const failed = index === 3 || index === 0 && sequence === 1, pending = index === 0 && sequence === 4
          // Two files in one sample task/round retain distinct document identities.
          let task = index === 2 ? tasks.get('preview-archive-task-A001-3')! : await withReportBytes(seed(`preview-archive-task-${short}-${sequence}`, sequence, failed, pending))
          if (scene === 'archive-report-failed' && index === 0 && sequence === 2) task = {...task, files: task.files.map(row => row.audit && row.file.original_filename === '控制计划.xlsx' ? {...row, audit: {...row.audit, report: {...row.audit.report, state: 'failed', artifact: null, generated_at: null, reason: 'PDF 生成失败。', regenerate_allowed: true, regenerate_reason: null}}} : row)}
          tasks.set(task.id, task)
          const file = task.files[index === 2 ? 0 : index === 3 ? 2 : 1], audit = file.audit!
          const entry: HistoryEntry = {document_id: documentId, reference: {task_id: task.id, round_id: task.round_id!, file_version_id: file.file.id, result_attempt: audit.result_attempt}, sequence, audited_at: `2026-10-${String(sequence === 4 ? 16 : sequence === 3 ? 15 : sequence === 2 ? 11 : 1).padStart(2, '0')}T09:00:00+08:00`, initiator: index === 1 ? '示例王工' : '示例李工', execution: audit.execution, conclusion: audit.conclusion, source_revision: sequence === 2 ? 'Rev B' : sequence === 3 ? 'Rev C' : null, report: structuredClone(audit.report), report_access: allowed}
          history.push(entry)
          details.set(resultKey(entry.reference), {entry, file: structuredClone(file), frozen_file_version_ids: task.files.filter(row => row.selection === 'selected').map(row => row.file.id), originals: task.files.filter(row => row.selection === 'selected').map(row => ({file_version_id: row.file.id, file: structuredClone(row.file), access: allowed})), source_task_access: scene === 'archive-task-allowed' ? allowed : denied, report_access: allowed})
        }
        const complete = history.find(row => row.execution === 'completed') || null
        documents.push({document_id: documentId, provider: 'feishu', title: index < 2 ? '控制计划.xlsx' : index === 2 ? 'PFMEA.xlsx' : index === 3 ? '过程流程图.pdf' : `控制计划-零件${String(index + 1).padStart(2, '0')}.xlsx`, metadata, document_type: index === 2 ? 'PFMEA' : index === 3 ? '过程流程图' : '控制计划', updated_at: history[0].audited_at, latest_completed: complete, latest_activity: history[0]})
        entries.set(documentId, history)
      }
    })()
    await initializing
  }
  return {
    listDocuments: async query => {
      await initialize()
      const filtered = documents.filter(row => row.title.toLowerCase().includes(query.search.toLowerCase()) && (!query.customer || row.metadata.customer === query.customer) && (!query.document_type || row.document_type === query.document_type) && (!query.initiator || row.latest_activity?.initiator === query.initiator) && (!query.from_date || row.updated_at.slice(0, 10) >= query.from_date) && (!query.through_date || row.updated_at.slice(0, 10) <= query.through_date) && (!query.category_id || query.category_id === 'cetus' && row.metadata.model === 'CETUS' || query.category_id === 'tail' && row.metadata.model === 'CETUS' && row.metadata.part === '尾门导流板' || query.category_id === 'fender' && row.metadata.part === '前翼子板' || query.category_id === 'other-model' && row.metadata.model === '其他示例车型' || query.category_id === 'unclassified' && row.metadata.model === null))
      return {query: structuredClone(query), documents: structuredClone(filtered.slice((query.page - 1) * 10, query.page * 10)), total: filtered.length, document_count: documents.length, categories: [{id: 'cetus', label: 'CETUS', count: 3, parts: [{id: 'tail', label: '尾门导流板', count: 2}, {id: 'fender', label: '前翼子板', count: 1}]}, {id: 'other-model', label: '其他示例车型', count: 8, parts: []}, {id: 'unclassified', label: '其他', count: 1, parts: []}], document_types: ['控制计划', 'PFMEA', '过程流程图'], customers: ['示例客户 A', '示例客户 B'], initiators: ['示例李工', '示例王工']}
    },
    listHistory: async documentId => {
      await initialize()
      if (scene === 'archive-history-list-error' && !historyListFailed) {historyListFailed = true; throw new Error('preview_history_list_failed')}
      const document = documents.find(row => row.document_id === documentId)
      if (!document) throw Object.assign(new Error('preview_document_missing'), {status: 404})
      return structuredClone({document, entries: entries.get(documentId)!, default_reference: document.latest_completed?.reference || null})
    },
    readHistory: async (documentId, reference) => {
      await initialize()
      const detail = details.get(resultKey(reference))
      if (!detail || detail.entry.document_id !== documentId) throw Object.assign(new Error('preview_history_missing'), {status: 404})
      const result = structuredClone(detail)
      let task = tasks.get(reference.task_id)
      if (task?.files.some(row => row.audit?.report.state === 'generating')) {
        task = await withReportBytes({...task, files: task.files.map(row => row.audit?.report.state === 'generating' ? {...row, audit: {...row.audit, report: {...fixtureReport(row.file.original_filename), report_id: row.audit.report.report_id, generation_request: row.audit.report.generation_request}}} : row)})
        tasks.set(task.id, task)
      }
      const file = task?.files.find(row => row.file.id === reference.file_version_id)
      if (file?.audit) {result.file = structuredClone(file); result.entry.report = structuredClone(file.audit.report)}
      const reportReference = historyReportReference(result.entry)
      result.report_share = reportReference ? readShare(reportReference) : null
      if (scene === 'archive-original-denied') result.originals = result.originals.map(row => ({...row, file: null, access: {state: 'denied', reason: '当前账号无权查看原件。'}}))
      if (['archive-report-deleted', 'archive-report-denied'].includes(scene) && result.entry.sequence === 2) {
        const access = {state: scene === 'archive-report-deleted' ? 'deleted' as const : 'denied' as const, reason: '所选历史报告已删除或访问权限已变更。'}
        result.report_access = access; result.entry.report_access = access
      }
      if (scene === 'archive-history-conflict') result.entry.conclusion = result.file.audit?.conclusion === 'PASS' ? 'NG' : 'PASS'
      if (scene === 'archive-share-scope-conflict') result.report_share = {request: {request_id: 'preview-wrong-share', organization_id: 'preview-org', reports: [{...reference, result_attempt: reference.result_attempt!, report_id: result.entry.report.report_id!, file_version_id: 'preview-other-file'}], targets: [], content: {kind: 'link', artifact: null, package_id: null}}, receipt: {request_id: 'preview-wrong-share', state: 'pending', reason: '分享记录与当前文件不匹配。'}, deliveries: []}
      return result
    },
  }
}

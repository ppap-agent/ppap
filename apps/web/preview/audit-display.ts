import type {AuditTask} from '../src/audit/intake'
import type {AuditDisplayAdapter} from '../src/audit/display-info-editor'
import {displayFields, fileDisplayValues, type DisplayReceipt, type DisplayRequest} from '../src/audit/display-info-contract'

/** UI fixture only: CAS/idempotency here do not establish real persistence. */
export function fixtureDisplay(tasks: Map<string, AuditTask>, scene: string): AuditDisplayAdapter {
  const receipts = new Map<string, DisplayReceipt>()
  let attempts = 0
  return {
    save: async request => {
      const existing = receipts.get(request.request_id)
      if (existing) return structuredClone(existing)
      attempts += 1
      const task = tasks.get(request.reference.task_id)
      const file = task?.round_id === request.reference.round_id ? task.files.find(row => row.file.id === request.reference.file_version_id) : undefined
      if (!file?.display_info || file.display_edit_access?.state !== 'allowed') return {request, state: 'rejected', info: null, reason: '示例：当前没有编辑许可，保存未执行。'}
      let receipt: DisplayReceipt
      if (scene === 'restore-display-rejected' && attempts === 1) receipt = {request, state: 'rejected', info: null, reason: '示例：保存未执行，输入仍保留。'}
      else {
        if (scene === 'restore-display-conflict' && attempts === 1) file.display_info = {revision: 1, values: {...file.display_info.values, customer: '其他操作保存的客户'}}
        if (file.display_info.revision !== request.expected_revision) receipt = {request, state: 'conflict', info: structuredClone(file.display_info), reason: '示例：其他操作已更新显示信息，请核对最新值。'}
        else {
          file.display_info = {revision: request.expected_revision + 1, values: structuredClone(request.values)}
          receipt = {request, state: 'saved', info: structuredClone(file.display_info), reason: null}
        }
      }
      receipts.set(request.request_id, structuredClone(receipt))
      if (scene === 'restore-display-unknown') throw new Error('preview_display_response_lost')
      if (scene === 'restore-display-invalid') return {...receipt, request: {...request, reference: {...request.reference, file_version_id: 'different-version'}}}
      return receipt
    },
    read: async (request: DisplayRequest) => structuredClone(receipts.get(request.request_id) || {request, state: 'unknown', info: null, reason: '示例：尚不能确认原请求。'}),
  }
}

export function displayReadFixture(task: AuditTask, scene: string): AuditTask {
  const next = structuredClone(task)
  next.files = next.files.map(file => {
    if (!file.display_info?.revision) return file
    if (scene === 'restore-display-missing') {delete file.display_info; return file}
    if (scene === 'restore-display-same-revision-conflict') return {...file, display_info: {...file.display_info, values: {...file.display_info.values, customer: '同版本错误值'}}}
    if (scene === 'restore-display-stale' || scene === 'restore-display-denied') return {...file, display_info: {revision: 0, values: Object.fromEntries(displayFields.map(([field]) => [field, file.match?.metadata[field] ?? null])) as ReturnType<typeof fileDisplayValues>}, display_edit_access: scene === 'restore-display-denied' ? {state: 'denied' as const, reason: '示例：编辑许可已撤销。'} : file.display_edit_access}
    return file
  })
  return next
}

import {apiUrl, readLocalUpload, request, uploadLocalVersion} from '../api.ts'
import type {components} from '../generated/openapi.ts'
import type {AuditIntakeAdapter, AuditTask} from './intake.tsx'
import type {AuditReviewAdapter} from './review-editor'
import type {AuditDisplayAdapter} from './display-info-editor'
import type {AuditReportAdapter} from './report-contract'

type ValidationJob = components['schemas']['FileValidationJob']
export const nativeReportAdapter: AuditReportAdapter = {
  async createPackage(reports, requestId) {
    try {return await request('/audit-report-packages', 'POST', {request_id: requestId, reports})}
    catch (failure) {
      const error = failure as {status?: number; code?: string}
      if (error.status !== 409 || error.code !== 'report_package_too_large') throw failure
      const reason = '所选报告总量过大，请减少本次打包的报告数量。'
      return {package_id: null, reports, receipt: {request_id: requestId, state: 'rejected', reason}, state: 'failed', artifact: null, reason, retry_allowed: false}
    }
  },
  async readPackage(requestId) {
    const packageJob = await request<components['schemas']['AuditReportPackage'] | null>(`/audit-report-packages/${encodeURIComponent(requestId)}`)
    if (!packageJob) throw new Error('尚未读到原打包请求，请继续核对。')
    return packageJob
  },
  async readPackageFile(job) {
    if (!job.package_id || !job.artifact) throw new Error('ZIP 尚未就绪。')
    const params = new URLSearchParams({package_id: job.package_id, artifact_id: job.artifact.artifact_id})
    const response = await fetch(`${apiUrl}/audit-report-packages/${encodeURIComponent(job.receipt.request_id)}/zip?${params}`, {method: 'GET', credentials: 'include'})
    if (!response.ok) {
      if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('ppap-session-expired'))
      throw new Error('ZIP 暂时无法读取。')
    }
    return response.blob()
  },
  async readPdf(reference, artifact) {
    const params = new URLSearchParams({round_id: reference.round_id, file_version_id: reference.file_version_id, result_attempt: String(reference.result_attempt), artifact_id: artifact.artifact_id})
    const response = await fetch(`${apiUrl}/audit-tasks/${encodeURIComponent(reference.task_id)}/reports/${encodeURIComponent(reference.report_id)}/pdf?${params}`, {method: 'GET', credentials: 'include'})
    if (!response.ok) {
      if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('ppap-session-expired'))
      throw new Error('审核报告暂时无法读取。')
    }
    return response.blob()
  },
}
export const nativeReviewAdapter: AuditReviewAdapter = {
  read: id => request(`/rounds/${encodeURIComponent(id)}`),
  save: (id, payload) => request(`/rounds/${encodeURIComponent(id)}/reviews`, 'PUT', payload),
}
export const nativeDisplayAdapter: AuditDisplayAdapter = {
  save: command => request(`/audit-tasks/${encodeURIComponent(command.reference.task_id)}/display-info`, 'POST', command),
  async read(command) {
    const receipt = await request<components['schemas']['AuditFileDisplayReceipt'] | null>(`/audit-tasks/${encodeURIComponent(command.reference.task_id)}/display-info/${encodeURIComponent(command.request_id)}`)
    return receipt ?? {request: command, state: 'unknown', info: null, reason: '尚未读到原保存请求的结果，请继续核对。'}
  },
}
const encoded = encodeURIComponent
const taskPath = (id: string) => `/audit-tasks/${encoded(id)}`
const removals = (task: AuditTask) => task.files.filter(row => row.selection === 'removed').map(row => ({file_version_id: row.file.id, removal_reason: row.removal_reason || ''}))
const confirmationRefusals: Record<string, string> = {
  audit_task_charging_unavailable: '当前环境尚未开放本次审核计费，审核没有开始。',
  task_revision_conflict: '任务文件已在其他页面更新。本次确认未受理，请核对当前任务。',
  task_not_ready: '文件识别尚未完成。本次确认未受理，请读取当前任务。',
  task_input_locked: '当前任务不能接受本次确认，请核对当前任务。',
  invalid_task_confirmation: '本次确认信息无效，审核没有开始。请核对当前任务。',
  target_required: '没有可进入审核的文件，本次确认未受理。',
}

/** This adapter owns transport only. Task, rules and execution remain server facts. */
export function createNativeAuditAdapter(options: {deepOffered: boolean; customers: string[]}): AuditIntakeAdapter {
  const validations = new Map<string, string>()
  function validation(job: ValidationJob, file: AuditTask['files'][number], requestId?: string) {
    if (requestId && job.id !== requestId || !job.id || job.source_file_id !== file.file.file_id || job.source_file_version_id !== file.file.id) throw new Error('预检查回执不属于这份文件版本或原请求。')
    validations.set(file.file.id, job.id)
    const result = job.result
    if (result && 'state' in result) return {validation_id: job.id, validation: result}
    return {validation_id: job.id, validation: {state: job.status === 'failed' ? 'error' as const : 'checking' as const, reasons: [job.status === 'failed' ? '文件预检查失败，请核对原文件。' : '正在检查文件内容…']}}
  }
  return {
    deepOffered: options.deepOffered,
    customers: options.customers.map(name => ({value: name, label: name})),
    autoValidate: true,
    upload: (input, requestId) => {
      if (input.kind !== 'local') throw new Error('飞书文件接收暂不可用，请先下载文件并本地上传。')
      return uploadLocalVersion(input.file, requestId)
    },
    readUpload: readLocalUpload,
    async readValidation(file) {
      const known = file.validation_id || validations.get(file.file.id)
      if (known) return validation(await request<ValidationJob>(`/file-validations/${encoded(known)}`), file, known)
      const latest = await request<components['schemas']['LatestFileValidation']>(`/files/${encoded(file.file.file_id)}/versions/${encoded(file.file.id)}/validation`)
      if (latest.validation) return validation(latest.validation, file)
      const id = crypto.randomUUID()
      // Remember before submission: a missing reply grants readback, not another job.
      validations.set(file.file.id, id)
      return validation(await request<ValidationJob>('/file-validations', 'POST', {request_id: id, source_file_id: file.file.file_id, source_file_version_id: file.file.id}), file, id)
    },
    match: (files, mode, customer, previous, requestId) => request<AuditTask>('/audit-tasks', 'POST', {
      request_id: requestId,
      analysis_mode: mode,
      customer_preference: customer,
      ...(previous ? {task_id: previous.id, expected_revision: previous.input_revision ?? 0} : {}),
      files: files.map(row => ({file_version_id: row.file.id, validation_id: row.validation_id ?? null, selection: row.selection, removal_reason: row.removal_reason})),
    }),
    readRecognition: (id, requestId) => request(`${taskPath(id)}/recognitions/${encoded(requestId)}`),
    readTask: (id, requestId) => request<AuditTask>(`${taskPath(id)}${requestId ? `?request_id=${encoded(requestId)}` : ''}`),
    async confirm(task, requestId) {
      try {return await request<AuditTask>(`${taskPath(task.id)}/confirm`, 'POST', {request_id: requestId, expected_revision: task.input_revision ?? 0})}
      catch (failure) {
        const error = failure as {status?: number; code?: string}
        const reason = error.code && confirmationRefusals[error.code]
        if (!reason || ![400, 409].includes(error.status || 0)) throw failure
        const current = await request<AuditTask>(`${taskPath(task.id)}?request_id=${encoded(requestId)}`)
        if (current.id !== task.id) throw new Error('任务回执不属于原确认请求。')
        // A transport observation of an explicit refusal, not a persisted server receipt.
        return current.state === 'pending_confirm' && !current.round_id ? {...current, acceptance: {request_id: requestId, state: 'rejected', reason}} : current
      }
    },
    endTask: task => request<AuditTask>(`${taskPath(task.id)}/end`, 'POST', {expected_revision: task.input_revision ?? 0, removals: removals(task)}),
    listTasks: ({q, state, offset}) => request(`/audit-tasks?${new URLSearchParams({q, state: state || '', offset: String(offset)})}`),
    authenticate: async () => {await request('/me')},
  }
}

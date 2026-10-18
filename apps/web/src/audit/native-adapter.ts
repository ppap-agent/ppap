import {apiUrl, readLocalUpload, request, uploadLocalVersion, withResponseTrace, type FeishuFileImportRequest} from '../api.ts'
import type {components} from '../generated/openapi.ts'
import type {AuditIntakeAdapter, AuditTask} from './intake.tsx'
import type {AuditReviewAdapter} from './review-editor'
import type {AuditDisplayAdapter} from './display-info-editor'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSupportAdapter, AuditTemplateAdapter} from './template-contract'
import {feedbackUploadError, type AuditFeedbackAdapter} from './feedback-contract.ts'
import type {UploadAdapter} from './uploads'
import type {AuditArchiveAdapter} from './archive-contract'
import type {AuditUsageAdapter} from './usage-contract'

export const nativeUsageAdapter: AuditUsageAdapter = {
  readUsage: query => request(`/audit-usage?${new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)]))}`),
}

export const nativeArchiveAdapter: AuditArchiveAdapter = {
  listDocuments(query) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) if (value !== null) params.set(key, String(value))
    return request(`/audit-archive?${params}`)
  },
  listHistory: documentId => request(`/audit-archive/${encodeURIComponent(documentId)}/history`),
  readHistory(documentId, reference) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(reference)) if (value !== null) params.set(key, String(value))
    return request(`/audit-archive/${encodeURIComponent(documentId)}/history/result?${params}`)
  },
}

const nativeUploads: UploadAdapter = {
  async upload(input, requestId) {
    if (input.kind === 'local') return uploadLocalVersion(input.file, requestId)
    try {
      await request<components['schemas']['FileVersion'], FeishuFileImportRequest>('/integrations/feishu/files/import', 'POST', {request_id: requestId, file_token: input.file.token, doc_type: input.file.type})
    } catch (failure) {
      const error = failure as {status?: number; code?: string; message?: string}
      if ([400, 401, 403, 404, 409, 413, 415, 422].includes(error.status || 0)) return {request_id: requestId, state: 'failed', file: null, reason: error.code === 'feishu_user_token_missing' ? '需要重新登录并授权飞书文件访问，也可以使用本地上传。' : error.message || '飞书文件未接收，请核对原文件。', retry_allowed: false}
    }
    try {return await readLocalUpload(requestId)}
    catch {return {request_id: requestId, state: 'unknown', file: null, reason: '未读到飞书导入结果，请读取原请求，不要再次添加文件。', retry_allowed: false}}
  },
  readUpload: readLocalUpload,
}

async function readTemplateBlob(path: string, expected: Pick<components['schemas']['FileVersion'], 'byte_size' | 'content_type'> & Partial<Pick<components['schemas']['FileVersion'], 'sha256'>>) {
  const response = await fetch(`${apiUrl}${path}`, {method: 'GET', credentials: 'include'})
  if (!response.ok) {
    if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('ppap-session-expired'))
    throw withResponseTrace(new Error('模板暂时无法读取，请核对飞书访问权限。'), response)
  }
  const blob = await response.blob()
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())), byte => byte.toString(16).padStart(2, '0')).join('')
  if (blob.size !== expected.byte_size || blob.type !== expected.content_type || response.headers.get('X-Content-SHA256') !== digest || expected.sha256 !== undefined && expected.sha256 !== digest) throw new Error('模板内容与所选版本不同，请重新读取。')
  return blob
}

export const nativeTemplateAdapter: AuditTemplateAdapter = {
  readCatalog: () => request('/audit-template-catalog'),
  listTemplates: query => request(`/audit-templates?${new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)]))}`),
  async readFile(item, purpose) {
    const reference = item.reference
    if (!item.file.sha256 || reference.file_version_id !== item.file.id) throw new Error('模板版本尚未就绪。')
    const params = new URLSearchParams({customer_id: reference.customer_id, purpose})
    const blob = await readTemplateBlob(`/audit-templates/${encodeURIComponent(reference.template_id)}/versions/${encodeURIComponent(reference.file_version_id)}?${params}`, item.file)
    return {reference, blob}
  },
  async readZip(folder) {
    const params = new URLSearchParams(Object.entries(folder).map(([key, value]) => [key, String(value)]))
    const snapshot = await request<components['schemas']['AuditTemplateZip']>(`/audit-template-packages?${params}`)
    if (snapshot.customer_id !== folder.customer_id || snapshot.folder_id !== folder.folder_id || snapshot.folder_revision !== folder.folder_revision || snapshot.templates.length !== folder.folder_template_count || !snapshot.complete || snapshot.access.state !== 'allowed') throw new Error('模板清单已变化，请重新读取。')
    const blob = await readTemplateBlob(`/audit-template-packages/download?${params}`, {content_type: 'application/zip', byte_size: snapshot.byte_size})
    return {snapshot, blob}
  },
}

export const nativeFeedbackAdapter: AuditFeedbackAdapter = {
  ...nativeUploads,
  listHistory: page => request(`/audit-template-feedback?${new URLSearchParams({page: String(page)})}`),
  submit: (input, attemptId) => request(`/audit-template-feedback/${encodeURIComponent(input.request_id)}/attempts/${encodeURIComponent(attemptId)}`, 'POST', input),
  read: (input, attemptId) => request(`/audit-template-feedback/${encodeURIComponent(input.request_id)}/attempts/${encodeURIComponent(attemptId)}`),
  upload(input, requestId) {
    const reason = input.kind !== 'local' ? '请使用本地模板附件。' : feedbackUploadError(input.file)
    return reason ? Promise.resolve({request_id: requestId, state: 'failed', file: null, reason, retry_allowed: false}) : nativeUploads.upload(input, requestId)
  },
  async readAttachment(file) {
    if (file.status !== 'ready' || !file.file_id || !file.id || !file.sha256 || !/^[a-f0-9]{64}$/.test(file.sha256)) throw new Error('附件版本尚未就绪，请读取原上传结果。')
    const path = `/files/${encodeURIComponent(file.file_id)}/versions/${encodeURIComponent(file.id)}`
    const stored = await request<components['schemas']['FileVersion']>(path)
    if (stored.id !== file.id || stored.file_id !== file.file_id || stored.status !== 'ready' || stored.original_filename !== file.original_filename || stored.content_type !== file.content_type || stored.byte_size !== file.byte_size || stored.sha256 !== file.sha256) throw new Error('附件版本与上传回执不同，请重新读取。')
    const response = await fetch(`${apiUrl}${path}/download`, {method: 'GET', credentials: 'include'})
    if (!response.ok) {
      if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('ppap-session-expired'))
      throw withResponseTrace(new Error('附件暂时无法读取，请重新读取。'), response)
    }
    const blob = await response.blob()
    if (blob.size !== stored.byte_size || blob.type !== stored.content_type || response.headers.get('X-Content-SHA256') !== stored.sha256) throw new Error('附件内容与已上传版本不同，未打开或下载。')
    const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())), byte => byte.toString(16).padStart(2, '0')).join('')
    if (sha256 !== stored.sha256) throw new Error('附件内容与已上传版本不同，未打开或下载。')
    return {file_version_id: stored.id, blob}
  },
}

export const nativeSupportAdapter: AuditSupportAdapter = {
  read: () => request('/support'),
  async readQr(configVersion) {
    const response = await fetch(`${apiUrl}/support/qr?${new URLSearchParams({config_version: configVersion})}`, {method: 'GET', credentials: 'include'})
    if (!response.ok) {
      if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('ppap-session-expired'))
      throw withResponseTrace(new Error('服务二维码暂时无法读取，请重新读取服务信息。'), response)
    }
    return response.blob()
  },
}

type ValidationJob = components['schemas']['FileValidationJob']
export const nativeReportAdapter: AuditReportAdapter = {
  async regenerate(reference, requestId) {
    const path = `/audit-tasks/${encodeURIComponent(reference.task_id)}/reports/${encodeURIComponent(reference.report_id)}/regenerations`
    try {return await request(path, 'POST', {request_id: requestId, reference})}
    catch (failure) {
      const error = failure as {status?: number; code?: string}
      if (error.status !== 409 || !['report_recovery_unavailable', 'audit_report_charging_unavailable'].includes(error.code || '')) throw failure
      const reason = error.code === 'audit_report_charging_unavailable' ? '当前环境尚未开放报告恢复计费，本次未重新生成。' : '这份报告当前不能重新生成，请读取最新状态。'
      return {reference, receipt: {request_id: requestId, state: 'rejected', reason}, report: null}
    }
  },
  async readRegeneration(reference, requestId) {
    const path = `/audit-tasks/${encodeURIComponent(reference.task_id)}/reports/${encodeURIComponent(reference.report_id)}/regenerations/${encodeURIComponent(requestId)}`
    const params = new URLSearchParams({round_id: reference.round_id, file_version_id: reference.file_version_id, result_attempt: String(reference.result_attempt)})
    const update = await request<components['schemas']['AuditReportUpdate'] | null>(`${path}?${params}`)
    return update ?? {reference, receipt: {request_id: requestId, state: 'pending', reason: '尚未读到原报告恢复请求，请继续读取，不重复生成。'}, report: null}
  },
  async createPackage(reports, requestId) {
    try {return await request('/audit-report-packages', 'POST', {request_id: requestId, reports})}
    catch (failure) {
      const error = failure as {status?: number; code?: string}
      if (error.status !== 409 || error.code !== 'report_package_too_large') throw failure
      const reason = '所选报告总量过大，请减少本次打包的报告数量。'
      return {package_id: null, reports, receipt: {request_id: requestId, state: 'rejected', reason}, state: 'failed', artifact: null, reason, retry_allowed: false}
    }
  },
  async retryPackage(job, requestId) {
    const rejected = (reason: string) => ({...job, receipt: {request_id: requestId, state: 'rejected' as const, reason}, retry_allowed: false, reason})
    if (!job.package_id || !Number.isSafeInteger(job.attempt) || job.attempt! < 1) return rejected('请先读取失败 ZIP 的当前状态，再重试打包。')
    try {return await request(`/audit-report-packages/${encodeURIComponent(job.receipt.request_id)}/retries`, 'POST', {request_id: requestId, package_id: job.package_id, expected_attempt: job.attempt})}
    catch (failure) {
      const error = failure as {status?: number; code?: string}
      if (error.status !== 409 || !['report_package_recovery_unavailable', 'audit_report_charging_unavailable'].includes(error.code || '')) throw failure
      return rejected(error.code === 'audit_report_charging_unavailable' ? '当前环境尚未开放 ZIP 恢复计费，本次未重新打包。' : 'ZIP 已有更新，本次未重新打包，请读取最新状态。')
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
      throw withResponseTrace(new Error('ZIP 暂时无法读取。'), response)
    }
    return response.blob()
  },
  async readPdf(reference, artifact) {
    const params = new URLSearchParams({round_id: reference.round_id, file_version_id: reference.file_version_id, result_attempt: String(reference.result_attempt), artifact_id: artifact.artifact_id})
    const response = await fetch(`${apiUrl}/audit-tasks/${encodeURIComponent(reference.task_id)}/reports/${encodeURIComponent(reference.report_id)}/pdf?${params}`, {method: 'GET', credentials: 'include'})
    if (!response.ok) {
      if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('ppap-session-expired'))
      throw withResponseTrace(new Error('审核报告暂时无法读取。'), response)
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
  rule_candidate_expired: '匹配的规则已有新版本，审核没有开始。请返回上一步重新识别，再核对规则。',
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
    notifications: {
      read: id => request(`${taskPath(id)}/notification`),
      save: (id, enabled, revision) => request(`${taskPath(id)}/notification`, 'PUT', {enabled, expected_revision: revision}),
    },
    customers: options.customers.map(name => ({value: name, label: name})),
    drive: {async list(pageToken) {
      try {return await request(`/integrations/feishu/files?${new URLSearchParams(pageToken ? {page_token: pageToken} : {})}`)}
      catch (failure) {
        if (failure instanceof Error && 'code' in failure && failure.code === 'feishu_user_token_missing') failure.message = 'authorization_required'
        throw failure
      }
    }},
    autoValidate: true,
    ...nativeUploads,
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

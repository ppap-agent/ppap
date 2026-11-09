import {apiUrl, readLocalUpload, request, uploadLocalVersion, withResponseTrace, type FeishuFileImportRequest} from '../api.ts'
import type {components, operations} from '../generated/openapi.ts'
import type {AuditIntakeAdapter, AuditTask} from './intake.tsx'
import type {AuditReviewAdapter} from './review-editor'
import type {AuditDisplayAdapter} from './display-info-editor'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSupportAdapter} from './support'
import type {UploadAdapter} from './uploads'
import type {AuditArchiveAdapter} from './archive-contract'
import type {AuditUsageAdapter} from './usage-contract'
import type {AuditSharingAdapter} from './sharing-contract'

export const nativeSharingAdapter: AuditSharingAdapter = {
  listTargets: (query, kind, pageToken) => readWithDeadline(signal => request(`/audit-share-targets?${new URLSearchParams({query, kind, ...(pageToken ? {page_token: pageToken} : {})})}`, 'GET', undefined, signal), '读取组织目录超时，请重新读取目标。'),
  async send(input) {
    try {return await request('/audit-report-shares', 'POST', input)}
    catch (failure) {
      const error = failure as {status?: number; code?: string}
      if (![400, 401, 403, 409, 422].includes(error.status || 0) || error.code === 'share_request_pending') throw failure
      const reason = error.code === 'share_file_too_large' ? '文件超过飞书消息的 30 MB 上限，请选择链接方式。' : error.code === 'share_target_unverified' ? '接收对象的组织或可用状态尚未核实，请重新选择。' : '分享未被接收，请核对登录、报告及接收范围。'
      return {request: input, receipt: {request_id: input.request_id, state: 'rejected', reason}, deliveries: input.targets.map(target => ({target: {...target, display_name: target.id, organization_id: input.organization_id, eligibility: 'unknown', reason}, attempt_id: input.request_id, state: 'failed', reason, retry_allowed: false}))}
    }
  },
  read: input => request(`/audit-report-shares/${encodeURIComponent(input.request_id)}?${new URLSearchParams({attempts: JSON.stringify(input.attempts)})}`),
  retryFailed: (requestId, target, attemptId) => request(`/audit-report-shares/${encodeURIComponent(requestId)}/retries`, 'POST', {target, attempt_id: attemptId}),
}

export function sharedReportAdapter(requestId: string): AuditReportAdapter {
  return {readPdf: (reference, artifact) => readFrozenBlob(`/audit-report-shares/${encodeURIComponent(requestId)}/pdf?${new URLSearchParams({report_id: reference.report_id, artifact_id: artifact.artifact_id})}`, artifact, '报告')}
}

export function readSharedArchive(requestId: string, artifact: components['schemas']['AuditReportArtifact']) {
  if (artifact.content_type !== 'application/zip') return Promise.reject(new Error('ZIP 产物不匹配。'))
  return readFrozenBlob(`/audit-report-shares/${encodeURIComponent(requestId)}/zip?${new URLSearchParams({artifact_id: artifact.artifact_id})}`, artifact, '报告 ZIP')
}

export const nativeUsageAdapter: AuditUsageAdapter = {
  readUsage: query => request(`/audit-usage?${new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)]))}`),
}

async function readWithDeadline<T>(read: (signal: AbortSignal) => Promise<T>, message: string): Promise<T> {
  const controller = new AbortController()
  const timeout = Object.assign(new Error(message), {name: 'TimeoutError'})
  let timer: ReturnType<typeof setTimeout> | undefined
  const expired = new Promise<never>((_resolve, reject) => {timer = setTimeout(() => {controller.abort(); reject(timeout)}, 45_000)})
  try {
    return await Promise.race([read(controller.signal), expired])
  } catch (error) {
    if (controller.signal.aborted) throw timeout
    throw error
  } finally {
    clearTimeout(timer)
  }
}

const readArchive = <T>(path: string) => readWithDeadline(signal => request<T>(path, 'GET', undefined, signal), '读取审核档案超时，请重新读取。')

export const nativeArchiveAdapter: AuditArchiveAdapter = {
  listDocuments(query) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) if (value !== null) params.set(key, String(value))
    return readArchive(`/audit-archive?${params}`)
  },
  listHistory: documentId => readArchive(`/audit-archive/${encodeURIComponent(documentId)}/history`),
  readHistory(documentId, reference) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(reference)) if (value !== null) params.set(key, String(value))
    return readArchive(`/audit-archive/${encodeURIComponent(documentId)}/history/result?${params}`)
  },
}

const nativeUploads: UploadAdapter = {
  async upload(input, requestId) {
    if (input.kind === 'local') {
      if (!(input.file instanceof File)) throw new Error('请重新选择当时的原件，再查看原请求。')
      const receipt = await uploadLocalVersion(input.file, requestId, undefined, input.destination?.token)
      if (!input.destination || receipt.state !== 'uploading' || receipt.file?.status !== 'ready' || !receipt.resume_allowed) return receipt
      try {return await request(`/file-uploads/${encodeURIComponent(requestId)}/feishu`, 'POST')}
      catch {
        try {return await readLocalUpload(requestId)}
        catch {return {...receipt, state: 'unknown', resume_allowed: false, reason: '未确认飞书上传结果，请查看原请求，不要重新上传。'}}
      }
    }
    try {
      await request<components['schemas']['FileVersion'], FeishuFileImportRequest>('/integrations/feishu/files/import', 'POST', {request_id: requestId, file_token: input.file.token, doc_type: input.file.type})
    } catch (failure) {
      const error = failure as {status?: number; code?: string; message?: string}
      if ([400, 401, 403, 404, 409, 413, 415, 422].includes(error.status || 0)) return {request_id: requestId, state: 'failed', file: null, reason: error.code === 'feishu_user_token_missing' ? '需要重新登录并授权飞书文件访问，也可以使用本地上传。' : error.message || '飞书文件未接收，请核对原文件。', retry_allowed: false}
    }
    try {return await readLocalUpload(requestId)}
    catch {return {request_id: requestId, state: 'unknown', file: null, reason: '未读到飞书导入结果，请查看原请求，不要再次添加文件。', retry_allowed: false}}
  },
  readUpload: readLocalUpload,
}

async function readFrozenBlob(path: string, expected: Pick<components['schemas']['FileVersion'], 'byte_size' | 'content_type'> & Partial<Pick<components['schemas']['FileVersion'], 'sha256'>>, label = '报告') {
  return readWithDeadline(async signal => {
    const response = await fetch(`${apiUrl}${path}`, {method: 'GET', credentials: 'include', signal})
    if (!response.ok) {
      if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('ppap-session-expired'))
      throw withResponseTrace(new Error(`${label}暂时无法读取，请核对飞书访问权限。`), response)
    }
    const blob = await response.blob()
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())), byte => byte.toString(16).padStart(2, '0')).join('')
    if (blob.size !== expected.byte_size || blob.type !== expected.content_type || response.headers.get('X-Content-SHA256') !== digest || expected.sha256 !== undefined && expected.sha256 !== digest) throw new Error(`${label}内容与所选版本不同，请重新读取。`)
    return blob
  }, `读取${label}超时，请重新读取。`)
}

export const nativeCustomerDefaultAdapter = {
  read: () => request<components['schemas']['AuditCustomerDefault']>('/audit-customers/default'),
  save: (customer: components['schemas']['AuditCustomerDefault']['customer_preference'], revision: number) => request<components['schemas']['AuditCustomerDefault']>('/audit-customers/default', 'PUT', {customer_preference: customer, expected_revision: revision, confirmed: true}),
}

export const readAuditCustomers = () => request<components['schemas']['AuditCustomerCatalog']>('/audit-customers')

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
const usageRefusals: Record<string, string> = {
  usage_quota_unavailable: '暂时无法确认飞书开关和额度，本次操作未被接收。请恢复授权后重试。',
  usage_ai_disabled: '飞书AI包开关已关闭，请联系管理员在飞书管理后台启用或联系我们获取免费额度',
  usage_balance_empty: '当前没有可用额度，本次操作未被接收。请联系管理员充值或联系我们获取免费额度。',
}
export const nativeReportAdapter: AuditReportAdapter = {
  async regenerate(reference, requestId) {
    const path = `/audit-tasks/${encodeURIComponent(reference.task_id)}/reports/${encodeURIComponent(reference.report_id)}/regenerations`
    try {return await request(path, 'POST', {request_id: requestId, reference})}
    catch (failure) {
      const error = failure as {status?: number; code?: string}
      if (error.status !== 409 || !['report_recovery_unavailable', 'audit_report_charging_unavailable', ...Object.keys(usageRefusals)].includes(error.code || '')) throw failure
      const reason = usageRefusals[error.code || ''] || (error.code === 'audit_report_charging_unavailable' ? '当前环境尚未开放报告恢复计费，本次未重新生成。' : '这份报告当前不能重新生成，请读取最新状态。')
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
      const reasons: Record<string, string> = {report_package_too_large: '所选报告总量过大，请减少本次打包的报告数量。', invalid_report_package: '本次未被接收，请重新核对所选报告。', report_not_ready: '所选报告尚未全部就绪，请刷新报告状态后再打包。'}
      const reason = reasons[error.code || '']
      if (![400, 409].includes(error.status || 0) || !reason) throw failure
      return {package_id: null, reports, receipt: {request_id: requestId, state: 'rejected', reason}, state: 'failed', artifact: null, reason, retry_allowed: false}
    }
  },
  async retryPackage(job, requestId) {
    const rejected = (reason: string) => ({...job, receipt: {request_id: requestId, state: 'rejected' as const, reason}, retry_allowed: false, reason})
    if (!job.package_id || !Number.isSafeInteger(job.attempt) || job.attempt! < 1) return rejected('请先读取失败 ZIP 的当前状态，再重试打包。')
    try {return await request(`/audit-report-packages/${encodeURIComponent(job.receipt.request_id)}/retries`, 'POST', {request_id: requestId, package_id: job.package_id, expected_attempt: job.attempt})}
    catch (failure) {
      const error = failure as {status?: number; code?: string}
      if (error.status !== 409 || !['report_package_recovery_unavailable', 'audit_report_charging_unavailable', ...Object.keys(usageRefusals)].includes(error.code || '')) throw failure
      return rejected(usageRefusals[error.code || ''] || (error.code === 'audit_report_charging_unavailable' ? '当前环境尚未开放 ZIP 恢复计费，本次未重新打包。' : 'ZIP 已有更新，本次未重新打包，请读取最新状态。'))
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
    return readWithDeadline(async signal => {
      const response = await fetch(`${apiUrl}/audit-report-packages/${encodeURIComponent(job.receipt.request_id)}/zip?${params}`, {method: 'GET', credentials: 'include', signal})
      if (!response.ok) {
        if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('ppap-session-expired'))
        throw withResponseTrace(new Error('ZIP 暂时无法读取。'), response)
      }
      return response.blob()
    }, '读取 ZIP 超时，请重新下载。')
  },
  async readPdf(reference, artifact) {
    const params = new URLSearchParams({round_id: reference.round_id, file_version_id: reference.file_version_id, result_attempt: String(reference.result_attempt), artifact_id: artifact.artifact_id})
    return readWithDeadline(async signal => {
      const response = await fetch(`${apiUrl}/audit-tasks/${encodeURIComponent(reference.task_id)}/reports/${encodeURIComponent(reference.report_id)}/pdf?${params}`, {method: 'GET', credentials: 'include', signal})
      if (!response.ok) {
        if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('ppap-session-expired'))
        throw withResponseTrace(new Error('审核报告暂时无法读取。'), response)
      }
      return response.blob()
    }, '读取审核报告超时，请重新读取。')
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
  ...usageRefusals,
  audit_task_charging_unavailable: '当前环境尚未开放本次审核计费，审核没有开始。',
  task_confirmation_changed: '审核依据已变化，审核没有开始。请刷新审核依据，核对后重新确认。',
  suite_validation_required: '当前检查版本需要重新验证，审核没有开始。请在检查版本工作区重新验证并发布，或明确确认采用未验证版本，再刷新审核依据。',
  suite_artifact_missing: '所选检查程序缺失，审核没有开始。请先恢复该版本的程序文件，再核对审核依据。',
  task_revision_conflict: '任务文件已在其他页面更新。本次确认未被接收，请核对当前任务。',
  task_not_ready: '文件识别尚未完成。本次确认未被接收，请读取当前任务。',
  rule_candidate_expired: '匹配的规则已有新版本，审核没有开始。请返回上一步重新识别，再核对规则。',
  task_input_locked: '当前任务不能接受本次确认，请核对当前任务。',
  invalid_task_confirmation: '本次确认信息无效，审核没有开始。请核对当前任务。',
  target_required: '没有可进入审核的文件，本次确认未被接收。',
  customer_scope_conflict: '本次文件属于不同客户，审核没有开始。请按客户分开任务后再确认。',
  precheck_blocked: '预检发现已认证的阻断问题，审核没有开始。请处理问题后重新预检。',
}

/** This adapter owns transport only. Task, rules and execution remain server facts. */
export function createNativeAuditAdapter(options: {deepOffered: boolean; customers: string[]; personalDriveUpload?: boolean}): AuditIntakeAdapter {
  const validations = new Map<string, string>()
  type Scope = components['schemas']['FileValidationScope']
  const sameScope = (left?: Scope | null, right?: Scope | null) => (!left && !right) || Boolean(left && right && left.customer_preference === right.customer_preference && left.preset_key === right.preset_key)
  const validationKey = (version: string, scope?: Scope | null) => `${version}:${scope ? 'UAES:U13' : 'ordinary'}`
  function validation(job: ValidationJob, file: AuditTask['files'][number], requestId?: string, scope?: Scope | null, restore = false) {
    if (requestId && job.id !== requestId || !job.id || job.source_file_id !== file.file.file_id || job.source_file_version_id !== file.file.id || !restore && !sameScope(job.scope, scope)) throw new Error('预检查结果不属于这份文件版本、用途或原请求。')
    if (job.scope && (job.scope.customer_preference !== 'UAES' || job.scope.preset_key !== 'U13')) throw new Error('预检查结果的用途无法核实。')
    validations.set(validationKey(file.file.id, job.scope), job.id)
    const result = job.result
    const purpose = job.scope ? {validation_scope: job.scope} : {}
    if (result && 'state' in result) return {validation_id: job.id, validation: result, ...purpose}
    return {validation_id: job.id, validation: {state: job.status === 'failed' ? 'error' as const : 'checking' as const, reasons: [job.status === 'failed' ? '文件预检查失败，请核对原文件。' : '正在检查文件内容…']}, ...purpose}
  }
  return {
    deepOffered: options.deepOffered,
    notifications: {
      read: id => request(`${taskPath(id)}/notification`),
      save: (id, enabled, revision) => request(`${taskPath(id)}/notification`, 'PUT', {enabled, expected_revision: revision}),
    },
    customerDefaults: nativeCustomerDefaultAdapter,
    customers: options.customers.map(name => ({value: name, label: name})),
    drive: {...options.personalDriveUpload ? {rootFolder: () => request<components['schemas']['DriveFile']>('/integrations/feishu/root-folder')} : {}, async list({folderToken, pageToken} = {}) {
      try {return await request(`/integrations/feishu/files?${new URLSearchParams({...folderToken ? {folder_token: folderToken} : {}, ...pageToken ? {page_token: pageToken} : {}})}`)}
      catch (failure) {
        if (failure instanceof Error && 'code' in failure && failure.code === 'feishu_user_token_missing') failure.message = 'authorization_required'
        throw failure
      }
    }},
    autoValidate: true,
    preparation: {
      read: id => request(`/drafts/${encoded(id)}`),
      save: (id, revision, value) => revision === null
        ? request('/drafts', 'POST', {request_id: id, upload_preparation: value})
        : request(`/drafts/${encoded(id)}`, 'PUT', {expected_revision: revision, upload_preparation: value}),
      readFile: (fileId, versionId) => request(`/files/${encoded(fileId)}/versions/${encoded(versionId)}`),
    },
    ...nativeUploads,
    async readValidation(file, requestedScope) {
      const scope = requestedScope === undefined ? file.validation_scope : requestedScope
      const key = validationKey(file.file.id, scope)
      const restore = requestedScope === undefined && Boolean(file.validation_id) && !file.validation_scope
      const known = (!file.validation_scope || sameScope(file.validation_scope, scope) ? file.validation_id : undefined) || validations.get(key)
      if (known) return validation(await request<ValidationJob>(`/file-validations/${encoded(known)}`), file, known, scope, restore)
      const latest = await request<components['schemas']['LatestFileValidation']>(`/files/${encoded(file.file.file_id)}/versions/${encoded(file.file.id)}/validation`)
      if (latest.validation && sameScope(latest.validation.scope, scope)) return validation(latest.validation, file, undefined, scope)
      const id = crypto.randomUUID()
      // Keep identity before submission, separately for each original and purpose.
      // A missing reply grants readback of that request, never a replacement POST.
      validations.set(key, id)
      try {return validation(await request<ValidationJob>('/file-validations', 'POST', {request_id: id, source_file_id: file.file.file_id, source_file_version_id: file.file.id, ...(scope ? {scope} : {})}), file, id, scope)}
      catch (failure) {
        // Preserve readback identity in the prepared file even if this adapter
        // is replaced after a customer-directory refresh or a page reload.
        throw Object.assign(failure instanceof Error ? failure : new Error('预检查结果尚未核对。'), {validationRequestId: id})
      }
    },
    precheck: {
      async history(id) {return (await request<components['schemas']['PrecheckHistory']>(`${taskPath(id)}/prechecks`)).snapshots},
      read: (id, snapshot) => request(`${taskPath(id)}/prechecks/${encoded(snapshot)}`),
      retry: (task, requestId) => request(`${taskPath(task.id)}/prechecks`, 'POST', {request_id: requestId, expected_revision: task.input_revision ?? 0}),
      async report(id, snapshot) {
        const response = await fetch(`${apiUrl}${taskPath(id)}/prechecks/${encoded(snapshot)}/report`, {credentials: 'include'})
        if (!response.ok || !response.headers.get('Content-Type')?.toLowerCase().startsWith('text/html')) throw new Error('预检记录尚不可读取。')
        return response.blob()
      },
    },
    prepareReaudit: roundId => request(`/rounds/${encoded(roundId)}/reaudit-inputs`),
    match: (files, mode, customer, previous, requestId, predecessorId, preparation) => request<AuditTask>('/audit-tasks', 'POST', {
      request_id: requestId,
      analysis_mode: mode,
      customer_preference: customer,
      ...(previous ? {task_id: previous.id, expected_revision: previous.input_revision ?? 0} : {}),
      ...(!previous && preparation ? {source_draft_id: preparation.id, expected_draft_revision: preparation.revision} : {}),
      ...((previous?.predecessor_id || predecessorId) ? {predecessor_id: previous?.predecessor_id || predecessorId} : {}),
      files: files.map(row => ({file_version_id: row.file.id, validation_id: row.validation_id ?? null, selection: row.selection, removal_reason: row.removal_reason, preferred_rule_version_id: row.preferred_rule_version_id, reference_only: row.reference_only, replacement_of_file_version_id: row.replacement_of_file_version_id})),
    }),
    readRecognition: (id, requestId) => request(`${taskPath(id)}/recognitions/${encoded(requestId)}`),
    readTask: (id, requestId) => request<AuditTask>(`${taskPath(id)}${requestId ? `?request_id=${encoded(requestId)}` : ''}`),
    async confirm(task, requestId) {
      if (!task.confirmation_basis_digest) throw new Error('请先读取当前审核依据，再确认审核。')
      try {return await request<AuditTask, operations['audit_tasks_confirm_audit_tasks__task_id__confirm_post']['requestBody']['content']['application/json']>(`${taskPath(task.id)}/confirm`, 'POST', {request_id: requestId, expected_revision: task.input_revision ?? 0, expected_confirmation_basis_digest: task.confirmation_basis_digest})}
      catch (failure) {
        const error = failure as {status?: number; code?: string}
        const reason = error.code && confirmationRefusals[error.code]
        if (!reason || ![400, 409].includes(error.status || 0)) throw failure
        const current = await request<AuditTask>(`${taskPath(task.id)}?request_id=${encoded(requestId)}`)
        if (current.id !== task.id) throw new Error('任务结果不属于原确认请求。')
        if (current.acceptance?.request_id === requestId && current.acceptance.state === 'rejected') return current
        // A transport observation of an explicit refusal, not a persisted server receipt.
        return current.state === 'pending_confirm' && !current.round_id ? {...current, acceptance: {request_id: requestId, state: 'rejected', reason}} : current
      }
    },
    endTask: task => request<AuditTask>(`${taskPath(task.id)}/end`, 'POST', {expected_revision: task.input_revision ?? 0, removals: removals(task), unselected_file_version_ids: task.files.filter(row => row.selection === 'unselected').map(row => row.file.id)}),
    listTasks: ({q, state, offset, signal}) => request(`/audit-tasks?${new URLSearchParams({q, state: state || '', offset: String(offset)})}`, 'GET', undefined, signal),
    authenticate: async () => {await request('/me')},
  }
}

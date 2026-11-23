import type {components, operations} from './generated/openapi'
import {receiptOf} from './audit/receipts-contract.ts'

type Schema<Name extends keyof components['schemas']> = components['schemas'][Name]
type JsonBody<Name extends keyof operations> = operations[Name] extends {requestBody?: infer Body}
  ? Body extends {content: {'application/json': infer Json}} ? Json : never
  : never

type BoundRule = Schema<'BoundRule'>
export type SemanticSource = Schema<'SemanticSource'>
type PublicConfig = Schema<'PublicConfig'>
export type PrincipalPublic = Schema<'PrincipalPublic'>
type FileVersion = Schema<'FileVersion'>
type UploadReceipt = Schema<'AuditUploadItem'>
export type RulesList = Schema<'RulesList'>
export type OfficePreviewResponse = Schema<'OfficePreviewResponse'>
export type HostLoginRequest = JsonBody<'login_feishu_host_login_feishu_host_post'>
type FileCreateRequest = JsonBody<'files_create_files_post'>
export type FeishuFileImportRequest = JsonBody<'feishu_files_import_integrations_feishu_files_import_post'>
export type ReviewRequest = JsonBody<'rounds_review_rounds__round_id__reviews_put'>

type MaterialWire = Schema<'MaterialWire'>
export type Material = Omit<MaterialWire, 'rule' | 'status'> & {
  rule?: BoundRule | null
  status?: string
}

export let apiUrl = ''
export let profile = ''
export let feishuAppId = ''
export let personalDriveUpload = false
export async function connect() {
  const runtime = await fetch('/runtime-config.json').then(r => r.json())
  apiUrl = runtime.api_url
  const config = await request<PublicConfig>('/public-config')
  apiUrl = runtime.api_url || config.api_url; profile = config.profile
  feishuAppId = config.feishu_app_id || ''
  personalDriveUpload = config.personal_drive_upload === true
  return config
}
const messages: Record<string,string> = {
  unauthenticated: '请先登录飞书。', platform_operator_required: '规则由平台统一维护，只有平台运营人员可以上传、发布或采用规则版本。', access_denied: '没有访问这份资料的权限。', file_not_ready: '文件尚未上传完成。',
  rule_required: '请为待审文件选择规则。', target_required: '请添加至少一份待审文件。', review_conflict: '复核已被更新，请刷新后核对。',
  draft_already_accepted: '此草稿已提交，请打开对应审核。',
  model_configuration_changed: '系统设置已更新，本次操作未被接收。请重新开始。',
  feedback_revision_conflict: '规则说明已被更新，请读取最新依据后再核对。',
  feedback_scope_conflict: '本次说明超出了可修改范围，请核对文件与要求。',
  feedback_question_expired: '该问题已随分析依据改变，请读取当前问题。',
  feedback_candidate_changed: '候选内容已变化，请重新核对实际内容。',
  feedback_question_changed: '问题内容或候选已经变化，请读取当前问题并重新核对。',
  invalid_feedback_request: '本次修改不完整，请核对说明内容与作用范围。',
  share_directory_permission_missing: 'PPAP 尚未获得所需的飞书目录权限，无法读取成员或群组。请联系组织管理员检查 PPAP 的授权。你也可以切回“人员”，清空搜索后向自己发送。',
  share_directory_user_permission_missing: '当前飞书登录尚未授予 PPAP 所需的目录访问权限。请检查飞书登录授权；如无法授权，请联系组织管理员。你也可以切回“人员”，清空搜索后向自己发送。',
  share_directory_timeout: '读取组织目录超时，请重新读取目标。',
  upload_request_conflict: '此传输请求已固定其他文件内容，不能替换。请核对原文件。',
  suite_key_mismatch: '这条规则不能写入所选检查版本。',
  model_unavailable: '服务暂时不可用，请稍后重试；如持续出现，请联系管理员。',
  model_consumption_refused: '暂时无法处理，请稍后重试。',
  duplicate_file_content: '有两份文件内容完全相同，请只保留其中一份再识别。',
  checker_not_generated: '这条规则还没有检查程序，不能用于审核。检查程序由平台离线准备，如需准备请联系我们。',
  questionnaire_closed: '问卷已关闭，答复不能再新增或修改。',
  answer_revision_conflict: '你的答复已在别处更新过，已重新读取，请核对后再保存。',
  answer_required: '请选择一项，或写下你的答案。',
  invalid_answer_request: '答复不完整，请重新选择后再保存。',
  reauthentication_required: '为确认是你本人在操作，请重新登录后再完成。',
  intent_unavailable: '这个请求已经处理过，或不再有效。',
  intent_mismatch: '这个请求不能用于当前操作。',
  content_changed: '报告内容刚刚有更新，已显示最新内容，请再确认一次。',
  feishu_user_token_missing: '请重新登录飞书后再生成报告文档。',
  report_pdf_not_ready: '报告 PDF 还没有全部生成，全部生成后才能在飞书里生成报告文档。',
  report_retry_unavailable: '这份报告文档正在处理，或这一步已经完成，已显示最新状态。',
  proposal_outdated: '这一步已经变化，请回到客户端按最新状态重新发起。',
  round_signed: '这一轮已经签发，撤回签发后才能更正复核。',
  round_already_signed: '这一轮已经签发，已显示最新状态。',
  round_not_signed: '这一轮已经不是签发状态，已显示最新状态。',
  round_not_finished: '审核结束、报告生成完后才能签发。',
  withdraw_reason_required: '请写明撤回原因。',
  withdraw_reason_too_long: '撤回原因最多 500 字。',
}

/** A signing action an external client proposed (#348): the request that completes it names it, and only that one. */
let activeIntent: {id: string; method: string; path: string} | null = null
export function setActiveIntent(intent: typeof activeIntent) {activeIntent = intent}
const intentHeader = (path: string, method: string): Record<string, string> => {
  const bare = path.split('?')[0]
  return activeIntent && (activeIntent.method === method && activeIntent.path === bare || method === 'POST' && bare === `/intents/${activeIntent.id}/decline`) ? {'X-PPAP-Intent': activeIntent.id} : {}
}
/** A signing call names its receipt (#361); the page shows it. One that completes a client's proposal is shown in the proposal. */
export const receiptIssued = 'ppap-receipt-issued'
const safeRequestTrace = (value: unknown) => typeof value === 'string' && /^[A-Za-z0-9._-]{8,128}$/.test(value) ? value : undefined
export const requestTrace = (cause: unknown) => safeRequestTrace(cause instanceof Error && 'traceId' in cause ? cause.traceId : undefined)
export function withResponseTrace<T extends Error>(error: T, response: Response): T {
  const traceId = safeRequestTrace(response.headers.get('X-Request-ID'))
  if (traceId) Object.assign(error, {traceId})
  return error
}
export async function request<T, B = unknown>(path: string, method = 'GET', body?: B, signal?: AbortSignal): Promise<T> {
  if (typeof navigator !== 'undefined' && !navigator.onLine) throw new Error('当前处于离线状态，请联网后重试。')
  const named = intentHeader(path, method)
  const response = await fetch(apiUrl + path, {method, signal, credentials: 'include', headers: {'Content-Type': 'application/json', ...named}, body: body === undefined ? undefined : JSON.stringify(body)})
  // Whatever became of a client's proposal, the banner reads it again.
  if (named['X-PPAP-Intent'] && typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('ppap-intent-changed', {detail: response.ok ? 'done' : ''}))
  // Only a write can be a signing action; reads carry no receipt.
  const receipt = method === 'GET' ? null : receiptOf(response.headers.get('X-PPAP-Action'))
  if (receipt && !named['X-PPAP-Intent'] && typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(receiptIssued, {detail: receipt}))
  const result = await response.json().catch(error => {
    if (signal?.aborted) throw error
    if (response.ok) throw withResponseTrace(new TypeError('服务器响应读取失败，请稍后重试。'), response)
    return {}
  })
  if (!response.ok) {
    const code = result && typeof result.error === 'string' ? result.error : undefined
    if (response.status === 401 && code === 'unauthenticated' && typeof window !== 'undefined') window.dispatchEvent(new Event('ppap-session-expired'))
    if (code === 'reauthentication_required' && typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('ppap-intent-changed', {detail: code}))
    // An unlisted code stays on the error for support; the person sees what happened and what to do.
    const error = new Error(code && messages[code] || (response.status >= 500 ? '审核服务暂时不可用，请稍后重试。' : '请求未完成，请稍后重试。'))
    Object.assign(error, {code, status: response.status})
    throw withResponseTrace(error, response)
  }
  return result as T
}
function checkLocalUpload(version: FileVersion, file: File, requestId: string, sha256: string) {
  const contentType = (file.type || 'application/octet-stream').split(';')[0].trim().toLowerCase()
  if (version.id !== requestId || !version.file_id || !version.original_filename.trim() || version.byte_size !== file.size || version.content_type.toLowerCase() !== contentType || version.sha256 !== null && version.sha256 !== sha256 || version.status === 'ready' && version.sha256 !== sha256) throw new Error('上传结果与原文件不一致，请先核对原传输请求。')
  return version
}
export async function readLocalUpload(requestId: string): Promise<UploadReceipt> {
  const receipt = await request<UploadReceipt>(`/file-uploads/${encodeURIComponent(requestId)}`)
  if (receipt.request_id !== requestId || receipt.file && receipt.file.id !== requestId || receipt.state === 'ready' && receipt.file?.status !== 'ready') throw new Error('上传结果与原请求不一致，请重新读取。')
  return receipt
}
const fileDigests = new WeakMap<File, Promise<string>>()
export function localFileDigest(file: File): Promise<string> {
  const known = fileDigests.get(file)
  if (known) return known
  const digest = file.arrayBuffer().then(bytes => crypto.subtle.digest('SHA-256', bytes)).then(bytes => Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join(''))
  fileDigests.set(file, digest)
  void digest.catch(() => fileDigests.delete(file))
  return digest
}
export async function uploadLocalVersion(file: File, requestId: string, onStaged?: (version: FileVersion) => void, folderToken?: string): Promise<UploadReceipt> {
  if (file.size > 200 * 1024 * 1024) throw new Error(`${file.name} 超过 200 MB。`)
  const sha256 = await localFileDigest(file)
  const input = {request_id: requestId, sha256, original_filename: file.name, byte_size: file.size, content_type: file.type || 'application/octet-stream', ...(folderToken ? {feishu_folder_token: folderToken} : {})}
  let staged: FileVersion | null = null
  function observe(version: FileVersion) {
    const next = checkLocalUpload(version, file, requestId, sha256)
    if (!staged) onStaged?.(next)
    staged = next
    return next
  }
  try {
    const version = observe(await request<FileVersion, FileCreateRequest>('/files', 'POST', input))
    if (version.status === 'ready') return folderToken ? readLocalUpload(requestId) : {request_id: requestId, state: 'ready', file: version, reason: null, retry_allowed: false}
    const path = `/files/${encodeURIComponent(version.file_id)}/versions/${encodeURIComponent(version.id)}/blob`
    const response = await fetch(apiUrl + path, {method: 'PUT', credentials: 'include', headers: {'Content-Type': file.type || 'application/octet-stream'}, body: file})
    if (!response.ok) {
      const error = new Error(`${file.name} 原件未接收（${response.status}），请核对原文件。`)
      Object.assign(error, {status: response.status})
      throw error
    }
    const ready = observe(await response.json() as FileVersion)
    if (ready.status !== 'ready') throw new Error('尚未取得文件已接收的结果，请查看原传输请求。')
    return folderToken ? readLocalUpload(requestId) : {request_id: requestId, state: 'ready', file: ready, reason: null, retry_allowed: false}
  } catch (error) {
    if (error instanceof Error && 'status' in error && [400, 401, 403, 404, 409, 413, 415, 422].includes(Number(error.status))) return {request_id: requestId, state: 'failed', file: staged, reason: error.message, retry_allowed: false}
    try {
      const receipt = await readLocalUpload(requestId)
      if (receipt.file) observe(receipt.file)
      return receipt
    } catch {
      return {request_id: requestId, state: 'unknown', file: staged, reason: '未取得明确传输结果，文件可能已接收。请查看原请求，不要重新上传。', retry_allowed: false}
    }
  }
}
export function originalUrl(file: Material, roundId?: string) {
  return `${apiUrl}${roundId ? `/rounds/${encodeURIComponent(roundId)}` : ''}/files/${encodeURIComponent(file.file_id)}/versions/${encodeURIComponent(file.file_version_id)}/download`
}

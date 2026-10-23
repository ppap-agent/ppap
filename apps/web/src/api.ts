import type {components, operations} from './generated/openapi'

type Schema<Name extends keyof components['schemas']> = components['schemas'][Name]
type JsonBody<Name extends keyof operations> = operations[Name] extends {requestBody?: infer Body}
  ? Body extends {content: {'application/json': infer Json}} ? Json : never
  : never

export type Rule = Schema<'Rule'>
export type BoundRule = Schema<'BoundRule'>
export type Analysis = Schema<'Analysis'>
export type RuleCoverageItem = Schema<'RuleCoverageItem'>
export type Finding = Schema<'Finding'>
export type FindingStatus = Finding['status']
export type SemanticSource = Schema<'SemanticSource'>
export type SemanticLocator = SemanticSource['locator']
export type SemanticEvidence = Schema<'SemanticEvidence'>
export type WritebackTarget = Schema<'WritebackTarget'>
export type QuotaEstimate = Schema<'QuotaEstimate'>
export type Round = Schema<'Round'>
export type Draft = Schema<'Draft'>
export type AnalysisMode = NonNullable<Draft['analysis_mode']>
export type AnalysisJob = Schema<'AnalysisJob'>
export type LatestAnalysis = Schema<'LatestAnalysis'>
export type RuleParseJob = Schema<'RuleParseJob'>
export type PublicConfig = Schema<'PublicConfig'>
export type PrincipalPublic = Schema<'PrincipalPublic'>
export type FileVersion = Schema<'FileVersion'>
export type UploadReceipt = Schema<'AuditUploadItem'>
export type DraftList = Schema<'DraftList'>
export type RoundList = Schema<'RoundList'>
export type RoundSummary = Schema<'RoundSummary'>
export type UsageEvent = Schema<'UsageEvent'>
export type UsageList = Schema<'UsageList'>
export type RulesList = Schema<'RulesList'>
export type DriveFile = Schema<'DriveFile'>
export type DriveFilesList = Schema<'DriveFilesList'>
export type DocLink = Schema<'DocLink'>
export type DocLinksResponse = Schema<'DocLinksResponse'>
export type PreviewResponse = Schema<'PreviewResponse'>
export type OfficePreviewResponse = Schema<'OfficePreviewResponse'>
export type ReportEnvelope = Schema<'ReportEnvelope'>
export type ReportExport = Schema<'ReportExport'>
export type WritebackResponse = Schema<'WritebackResponse'>
export type RuleTypes = Schema<'RuleTypes'>
export type RuleDetail = Schema<'RuleDetail'>
export type RuleBody = Schema<'EngineRuleBody'>
export type RuleItem = Schema<'RuleItem'>
export type RuleCandidate = Schema<'RuleCandidate'>
export type RuleSources = Schema<'RuleSources'>
export type HostLoginRequest = JsonBody<'login_feishu_host_login_feishu_host_post'>
export type FileCreateRequest = JsonBody<'files_create_files_post'>
export type FileFinalizeRequest = JsonBody<'files_finalize_files__file_id__versions__version_id__finalize_post'>
export type FeishuFileImportRequest = JsonBody<'feishu_files_import_integrations_feishu_files_import_post'>
export type DraftCreateRequest = JsonBody<'drafts_create_drafts_post'>
export type DraftSaveRequest = JsonBody<'drafts_save_drafts__draft_id__put'>
export type DraftAcceptRequest = JsonBody<'drafts_accept_drafts__draft_id__accept_post'>
export type FileAnalysisRequest = JsonBody<'files_analyze_file_analyses_post'>
export type ReviewRequest = JsonBody<'rounds_review_rounds__round_id__reviews_put'>
export type RuleParseRequest = JsonBody<'rules_parse_rules_parse_post'>
export type RulePublishRequest = JsonBody<'rules_publish_rules_post'>

type MaterialWire = Schema<'MaterialWire'>
export type Material = Omit<MaterialWire, 'rule' | 'status'> & {
  rule?: BoundRule | null
  status?: string
  analysis?: Analysis
  analysisId?: string
  analysisStatus?: string
  analysisReadFailed?: boolean
}

export class UploadResultUnknown extends Error {
  material: Material
  requestId?: string
  constructor(material: Material, requestId?: string) {
    super(`上传结果未确认：${material.original_filename}。请核对该文件版本，避免重复上传。`)
    this.material = material
    this.requestId = requestId
  }
}
export const resultLabels:Record<FindingStatus,string> = {PASS:'通过', NG:'不符合', ERR:'检查异常', UNABLE:'无法判断', NA:'不适用'}
export const executionLabels:Record<string,string> = {queued:'排队中', running:'检查中', completed:'检查完成', failed:'执行失败'}
export const analysisModeLabels:Record<AnalysisMode,string> = {standard:'标准', deep:'深度'}
export function dateLabel(value?: string | null) {
  return value ? new Intl.DateTimeFormat('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)) : '—'
}
export let apiUrl = ''
export let profile = ''
export let deepOffered = false
export let feishuAppId = ''
export let personalDriveUpload = false
export async function connect() {
  const runtime = await fetch('/runtime-config.json').then(r => r.json())
  apiUrl = runtime.api_url
  const config = await request<PublicConfig>('/public-config')
  apiUrl = runtime.api_url || config.api_url; profile = config.profile
  deepOffered = config.deep_offered === true
  feishuAppId = config.feishu_app_id || ''
  personalDriveUpload = config.personal_drive_upload === true
  return config
}
const messages: Record<string,string> = {
  unauthenticated: '请先登录飞书。', access_denied: '没有访问这份资料的权限。', file_not_ready: '文件尚未上传完成。',
  rule_required: '请为待审文件选择规则。', target_required: '请添加至少一份待审文件。', review_conflict: '复核已被更新，请刷新后核对。',
  draft_already_accepted: '此草稿已提交，请打开对应审核。', rule_parse_failed: '规则解析失败，请检查原文件或文档类型。',
  rule_source_unverified: '条款出处与原件不一致。请重新选择原文位置并核对，草稿未发布。',
  rule_source_unavailable: '暂时无法读取可定位的原文。请核对原文件是否可读取。',
  invalid_analysis_mode: '请选择标准或深度审核。',
  deep_not_available: '当前环境不提供深度审核。',
  share_directory_permission_missing: 'PPAP 尚未获得所需的飞书目录权限，无法读取成员或群组。请联系组织管理员检查 PPAP 的授权。你也可以切回“人员”，清空搜索后向自己发送。',
  share_directory_user_permission_missing: '当前飞书登录尚未授予 PPAP 所需的目录访问权限。请检查飞书登录授权；如无法授权，请联系组织管理员。你也可以切回“人员”，清空搜索后向自己发送。',
  share_directory_timeout: '读取组织目录超时，请重新读取目标。',
  upload_request_conflict: '此传输请求已固定其他文件内容，不能替换。请核对原文件。',
}
const safeRequestTrace = (value: unknown) => typeof value === 'string' && /^[A-Za-z0-9._-]{8,128}$/.test(value) ? value : undefined
export const requestTrace = (cause: unknown) => safeRequestTrace(cause instanceof Error && 'traceId' in cause ? cause.traceId : undefined)
export function withResponseTrace<T extends Error>(error: T, response: Response): T {
  const traceId = safeRequestTrace(response.headers.get('X-Request-ID'))
  if (traceId) Object.assign(error, {traceId})
  return error
}
export async function request<T, B = unknown>(path: string, method = 'GET', body?: B, signal?: AbortSignal): Promise<T> {
  if (typeof navigator !== 'undefined' && !navigator.onLine) throw new Error('当前处于离线状态，请联网后重试。')
  const response = await fetch(apiUrl + path, {method, signal, credentials: 'include', headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)})
  const result = await response.json().catch(error => {
    if (signal?.aborted) throw error
    if (response.ok) throw withResponseTrace(new TypeError('服务器响应读取失败，请稍后重试。'), response)
    return {}
  })
  if (!response.ok) {
    const code = result && typeof result.error === 'string' ? result.error : undefined
    if (response.status === 401 && code === 'unauthenticated' && typeof window !== 'undefined') window.dispatchEvent(new Event('ppap-session-expired'))
    const error = new Error(code && messages[code] || `请求失败（${response.status}）：${code || '请稍后重试'}`)
    Object.assign(error, {code, status: response.status})
    throw withResponseTrace(error, response)
  }
  return result as T
}
function checkLocalUpload(version: FileVersion, file: File, requestId: string, sha256: string) {
  const contentType = (file.type || 'application/octet-stream').split(';')[0].trim().toLowerCase()
  if (version.id !== requestId || !version.file_id || !version.original_filename.trim() || version.byte_size !== file.size || version.content_type.toLowerCase() !== contentType || version.sha256 !== null && version.sha256 !== sha256 || version.status === 'ready' && version.sha256 !== sha256) throw new Error('上传回执与原文件不一致，请先核对原传输请求。')
  return version
}
export async function readLocalUpload(requestId: string): Promise<UploadReceipt> {
  const receipt = await request<UploadReceipt>(`/file-uploads/${encodeURIComponent(requestId)}`)
  if (receipt.request_id !== requestId || receipt.file && receipt.file.id !== requestId || receipt.state === 'ready' && receipt.file?.status !== 'ready') throw new Error('上传回执与原请求不一致，请重新读取。')
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
    if (ready.status !== 'ready') throw new Error('尚未取得文件已接收的回执，请读取原传输请求。')
    return folderToken ? readLocalUpload(requestId) : {request_id: requestId, state: 'ready', file: ready, reason: null, retry_allowed: false}
  } catch (error) {
    if (error instanceof Error && 'status' in error && [400, 401, 403, 404, 409, 413, 415, 422].includes(Number(error.status))) return {request_id: requestId, state: 'failed', file: staged, reason: error.message, retry_allowed: false}
    try {
      const receipt = await readLocalUpload(requestId)
      if (receipt.file) observe(receipt.file)
      return receipt
    } catch {
      return {request_id: requestId, state: 'unknown', file: staged, reason: '未取得明确传输结果，文件可能已接收。请读取原请求，不要重新上传。', retry_allowed: false}
    }
  }
}
export async function upload(file: File, role: Material['role'], onStaged?: (material: Material) => void): Promise<Material> {
  const requestId = crypto.randomUUID()
  let staged: Material | undefined
  const receipt = await uploadLocalVersion(file, requestId, version => {
    staged = {file_id: version.file_id, file_version_id: version.id, original_filename: version.original_filename, role, rule_version_id: null}
    onStaged?.(staged)
  })
  if (receipt.state === 'ready' && staged) return staged
  if (staged && ['unknown', 'uploading'].includes(receipt.state)) throw new UploadResultUnknown(staged, requestId)
  const error = new Error(receipt.reason || `上传结果未确认：${file.name}。请读取原传输请求，不要重新上传。`)
  Object.assign(error, {requestId})
  throw error
}
export function originalUrl(file: Material, roundId?: string) {
  return `${apiUrl}${roundId ? `/rounds/${encodeURIComponent(roundId)}` : ''}/files/${encodeURIComponent(file.file_id)}/versions/${encodeURIComponent(file.file_version_id)}/download`
}
export function bundleUrl(roundId: string) {
  return `${apiUrl}/rounds/${encodeURIComponent(roundId)}/bundle`
}

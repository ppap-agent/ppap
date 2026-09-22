export interface Rule {
  id: string; title: string; kind: string; type?: string; created_at: string; predecessor_id?: string | null
  customers?: string[]; models?: string[]; parts?: string[]
}
export interface Material {
  file_id: string; file_version_id: string; original_filename: string; role: 'target' | 'reference'
  rule_version_id: string | null; rule?: Rule; analysis?: Analysis; analysisId?: string; analysisStatus?: string
}
export interface Analysis {
  metadata: {customer: string | null; model: string | null; part: string | null}
  document_type: string | null; matched_rule_id: string | null; matching_status: string; notices: string[]
}
export interface RuleCoverageItem {
  id: string; file_version_id?: string; category?: string; priority?: string | null; rule_to_check: string
  coverage: 'mapped' | 'unsupported'; reason?: string; item_key?: string; status?: string
}
export interface Finding {
  item_key: string; status: 'PASS' | 'NG' | 'ERR'; item: string; classification: string; suggestions: string; priority?: string
  locator?: {file_id?: string; file_version_id?: string; filename?: string; locator?: string; rule_item_id?: string; evidence?: {sources?: {kind: string; locator: string}[]}}
  review?: {judgment: string; rationale: string; next_step: string; version: number}
}
export interface Round {
  id: string; execution: string; inputs: Material[]; findings: Finding[]; predecessor_id?: string
  created_at?: string; result_counts?: Record<string, number>; reviewed_count?: number
  coverage?: {checked: string[]; unchecked: string[]; locator_gap: boolean; rule_items?: RuleCoverageItem[]}
  analysis_mode?: AnalysisMode; executor?: string; executor_revision?: string
}
export interface Draft {id:string; status:string; items:Material[]; updated_at?:string; predecessor_id?:string|null; analysis_mode?: AnalysisMode}
export type AnalysisMode = 'standard' | 'deep'
export const resultLabels:Record<string,string> = {PASS:'通过', NG:'不符合', ERR:'检查异常'}
export const executionLabels:Record<string,string> = {queued:'排队中', running:'检查中', completed:'检查完成', failed:'执行失败'}
export const analysisModeLabels:Record<AnalysisMode,string> = {standard:'标准', deep:'深度'}
export function dateLabel(value?:string) {
  return value ? new Intl.DateTimeFormat('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)) : '—'
}
export {latestRules} from './helpers'
export interface Job<T> {id: string; status: string; result?: T & {error?: string}}
export let apiUrl = ''
export let profile = ''
export async function connect() {
  const runtime = await fetch('/runtime-config.json').then(r => r.json())
  apiUrl = runtime.api_url
  const config = await request<{api_url: string; profile: string}>('/public-config')
  apiUrl = config.api_url; profile = config.profile
}
const messages: Record<string,string> = {
  unauthenticated: '请先登录飞书。', access_denied: '没有访问这份资料的权限。', file_not_ready: '文件尚未上传完成。',
  rule_required: '请为待审文件选择规则。', target_required: '请添加至少一份待审文件。', review_conflict: '复核已被更新，请刷新后核对。',
  draft_already_accepted: '此草稿已提交，请打开对应审核。', rule_parse_failed: '规则解析失败，请检查原文件或文档类型。',
  invalid_analysis_mode: '请选择标准或深度审核。',
}
export async function request<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(apiUrl + path, {method, signal, credentials: 'include', headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)})
  const result = await response.json().catch(error => {if(signal?.aborted)throw error;return {}})
  if (!response.ok) throw new Error(messages[result.error] || `请求失败（${response.status}）：${result.error || '请稍后重试'}`)
  return result as T
}
export async function upload(file: File, role: Material['role']): Promise<Material> {
  if (file.size > 64 * 1024 * 1024) throw new Error(`${file.name} 超过 64 MiB。`)
  const version = await request<{file_id: string; id: string}>('/files', 'POST', {original_filename: file.name, byte_size: file.size, content_type: file.type || 'application/octet-stream'})
  const path = `/files/${version.file_id}/versions/${version.id}`
  const response = await fetch(apiUrl + path + '/blob', {method: 'PUT', credentials: 'include', headers: {'Content-Type': file.type || 'application/octet-stream'}, body: file})
  if (!response.ok) throw new Error(`${file.name} 上传失败，请重新选择文件。`)
  await request(path + '/finalize', 'POST', {})
  return {file_id: version.file_id, file_version_id: version.id, original_filename: file.name, role, rule_version_id: null}
}
export function originalUrl(file: Material, roundId?: string) {
  return `${apiUrl}${roundId ? `/rounds/${encodeURIComponent(roundId)}` : ''}/files/${encodeURIComponent(file.file_id)}/versions/${encodeURIComponent(file.file_version_id)}/download`
}
export function bundleUrl(roundId: string) {
  return `${apiUrl}/rounds/${encodeURIComponent(roundId)}/bundle`
}

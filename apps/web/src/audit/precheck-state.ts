import type {components} from '../generated/openapi.ts'

type Task = components['schemas']['AuditTask']
export type PrecheckReceipt = components['schemas']['PrecheckReceipt']
export type PrecheckFact = {id: string; status: string; message: string; nextStep: string; code: string; certainty: string; critical: boolean; certified: boolean; evidence: {locator: string; excerpt: string}[]}
export type PreparationAction = 'ready' | 'process' | 'confirm'
export const preparationLabels = {ready: '可审核', process: '需要处理', confirm: '需要确认'} as const
const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const string = (value: unknown) => typeof value === 'string' ? value : ''

/** Interpret only documented facts. Missing or unfamiliar observations stay unknown. */
export function precheckFacts(snapshot: PrecheckReceipt | null | undefined, version: string): PrecheckFact[] {
  const files = record(snapshot?.result).files
  if (!Array.isArray(files)) return []
  const row = files.map(record).find(row => row.file_version_id === version)
  if (!Array.isArray(row?.facts)) return []
  return row.facts.map(record).map(fact => ({
    id: string(fact.id), status: string(fact.status) || 'unknown', message: string(fact.message) || '本项尚未核实。',
    nextStep: string(fact.next_step), code: string(fact.code), certainty: string(fact.certainty), critical: fact.critical === true, certified: fact.certified === true,
    evidence: Array.isArray(record(fact.evidence).sources) ? (record(fact.evidence).sources as unknown[]).map(record).map(source => ({locator: string(source.locator), excerpt: string(source.excerpt)})) : [],
  }))
}

export function preparationAction(file: Task['files'][number], facts: PrecheckFact[]): PreparationAction {
  if (file.validation.state === 'invalid') return 'process'
  if (file.validation.state !== 'valid') return 'confirm'
  if (file.suite_admission?.state === 'validation_required') return 'confirm'
  const match = file.match
  if (match?.gaps.some(gap => ['matching_customer_required', 'matching_customer_conflict', 'matching_document_type_required', 'ambiguous_available_rules', 'matching_rules_customer_unassigned', 'matching_snapshot_invalid', 'matching_result_unknown'].includes(gap))) return 'confirm'
  if (match?.state === 'unmatched' && match.document_type && match.gaps.some(gap => ['matching_document_type_rules_missing', 'matching_customer_rules_missing', 'no_available_rules'].includes(gap))) return 'process'
  if (!match || !['matched', 'reference'].includes(match.state) || match.state === 'matched' && !match.rule) return 'confirm'
  // Preparation actions are not an acceptance gate. Ordinary warnings,
  // missing implementation and execution failures remain visible details.
  if (facts.some(fact => fact.code === 'B03' && ['unknown', 'defect'].includes(fact.status))) return 'confirm'
  if (facts.some(fact => fact.status === 'defect' && fact.certainty === 'confirmed' && fact.critical && ['B01', 'B02', 'B04', 'B05', 'B06'].includes(fact.code) && fact.evidence.some(source => {
    try {
      const locator = record(JSON.parse(source.locator))
      return locator.file_version_id === file.file.id && Boolean(source.excerpt.trim()) && Boolean(locator.cell || locator.range || locator.page)
    } catch {return false}
  }))) return 'process'
  return 'ready'
}

export function precheckIsCurrent(snapshot: PrecheckReceipt, task: Pick<Task, 'id' | 'input_revision'>) {
  return snapshot.task_id === task.id && snapshot.input_revision === (task.input_revision ?? 0) && snapshot.current
}

/** Visibility follows server-owned preparation or Suite identity, never a legacy profile. */
export function hasTaskPrecheck(task: Pick<Task, 'files' | 'precheck'>) {
  return Boolean(task.precheck) || task.files.some(file => file.selection === 'selected' && file.match?.state === 'matched' && file.match.rule?.body.kind === 'engine' && Boolean(string(record(file.match.rule.body).suite_key).trim()))
}

export function checkerLabel(file: Task['files'][number], snapshot?: PrecheckReceipt | null) {
  if (file.match?.state === 'reference') return '比对资料 · 不单独审核或收费'
  const rule = file.match?.rule, body = rule?.body
  if (body?.kind === 'presence') return '文件存在性检查'
  if (!body || body.kind !== 'engine') return '尚未确定适用规则'
  const rows = record(snapshot?.input).files
  const frozen = Array.isArray(rows) ? record(rows.find(row => record(record(row).file).id === file.file.id)) : {}
  const target = record(frozen.suite_target), execution = record(frozen.rule_execution)
  const title = string(record(target.rules).title) || string(record(body).title) || rule?.title || `${body.type}检查`
  const notices: string[] = []
  if (file.suite_admission?.state === 'validation_required') notices.push('检查版本需要重新验证，暂不能开始审核')
  if (file.suite_admission?.force_unverified && file.suite_admission.verification_status !== 'verified') notices.push('已明确采用未验证版本')
  if (file.suite_admission?.samples_not_rerun) notices.push('平台已更新，样本未重跑')
  if (file.match?.gaps?.includes('published_rules_changed')) notices.push('新规则尚未采用，本次按旧规则和已发布检查运行')
  if (notices.length) return [title, ...notices].join(' · ')
  if (execution.mode === 'checker_core' && string(target.suite_revision_id) && string(target.suite_key)) return `${title} · 本次采用的检查${target.verification === 'verified' ? '' : ' · 需要重新验证'}`
  if (execution.mode === 'unavailable') return `${title} · 尚无可运行的检查，未检查`
  if (string(record(body).suite_key).trim()) return `${title} · 按本企业采用版本预检`
  return `${title} · 检查来源尚未确认，不代表已检查`
}

export function precheckFiles(snapshot: PrecheckReceipt): {version: string; name: string}[] {
  const files = record(snapshot.input).files
  if (!Array.isArray(files)) return []
  return files.map(record).map(row => record(row.file)).filter(file => typeof file.id === 'string').map(file => ({version: string(file.id), name: string(file.original_filename) || '原件'}))
}

export function evidenceLocation(locator: string) {
  try {
    const value = record(JSON.parse(locator))
    if (typeof value.sheet === 'string' && typeof value.cell === 'string') return `${value.sheet}!${value.cell}`
    if (typeof value.page === 'number') return `原件第 ${value.page} 页`
    if ((typeof value.table === 'string' || typeof value.table === 'number') && value.row && value.column) return `${value.table} · 行 ${String(value.row)} · 列 ${String(value.column)}`
    if (value.paragraph) return `原件段落 ${String(value.paragraph)}`
  } catch { /* A malformed locator does not become a clickable source. */ }
  return '原件定位待确认'
}


export function precheckOriginals(snapshot: PrecheckReceipt): components['schemas']['FileVersion'][] {
  const files = record(snapshot.input).files
  if (!Array.isArray(files)) return []
  return files.map(record).map(row => record(row.file)).flatMap(file => {
    if (typeof file.id !== 'string' || typeof file.file_id !== 'string' || typeof file.original_filename !== 'string' || typeof file.content_type !== 'string' || typeof file.status !== 'string' || typeof file.byte_size !== 'number' || !Number.isSafeInteger(file.byte_size) || file.byte_size < 0) return []
    return [{id: file.id, file_id: file.file_id, original_filename: file.original_filename, content_type: file.content_type, status: file.status, byte_size: file.byte_size, sha256: typeof file.sha256 === 'string' ? file.sha256 : null}]
  })
}


export function evidenceVersion(locator: string): string | null {
  try {const value = record(JSON.parse(locator)); return typeof value.file_version_id === 'string' ? value.file_version_id : null}
  catch {return null}
}

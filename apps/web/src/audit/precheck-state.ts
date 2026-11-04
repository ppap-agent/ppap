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

export function checkerLabel(file: Task['files'][number], snapshot?: PrecheckReceipt | null) {
  const body = file.match?.rule?.body
  if (body?.kind === 'presence') return '文件存在性检查'
  if (!body || body.kind !== 'engine') return file.match?.state === 'reference' ? '比对资料 · 不单独审核或收费' : '尚未确定适用规则'
  const rows = record(snapshot?.result).files
  const profile = Array.isArray(rows) ? record(record(rows.find(row => record(row).file_version_id === file.file.id)).checker).profile : null
  if (profile === 'v2' || profile === 'v3') return `使用通用 ${profile.toUpperCase()} 检查，客户专属条款可能未覆盖`
  if (body.rule_profile === 'v4') return `${body.customers?.join('、') || '客户'} V4 检查`
  return `使用通用 ${body.rule_profile === 'v3' ? 'V3' : 'V2'} 检查，客户专属条款可能未覆盖`
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

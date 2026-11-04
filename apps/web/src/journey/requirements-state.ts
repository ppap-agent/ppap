// Requirement snapshots. The three known preview clauses are synthetic, not a PPAP standard.
import {isObject} from './saved-draft.ts'
import {optionalLocalFile, type LocalFileRef} from './local-files.ts'

export type RequirementClause = {
  id: string; title: string; text: string; original: string; source: string; location: string
  applicability: 'pending' | 'apply' | 'exclude'; reason: string; checked: boolean
  provenance?: {id: string; location: string; excerpt: string; fileVersionId: string}
}
export type RequirementBasis = {
  source: {id: string; title: string; filename: string; version: string; origin: 'demo' | 'local'; state: 'ready' | 'unparsed' | 'failed' | 'manual'; localFile?: LocalFileRef}
  clauses: RequirementClause[]
  conflict: {active: boolean; choice: 'pending' | 'customer' | 'internal'; reason: string}
  confirmed: {at: string; by: string; scope: string; content: string} | null
  library?: {collectionId: string; versionId: string; content: string}
  server?: {body: import('../api').RuleBody; material: import('../api').Material; sources?: import('../api').RuleSources; ruleId?: string; parseId?: string; publishedContent?: string; verifiedContent?: string}
}
export const customerSource = 'P204_客户审核要求_v2.pdf'
export const internalSource = '内部尺寸模板_v1.pdf'
const texts = [
  '流程图与控制计划中，同一工序号的工序名称应一致。此项只核对文件一致性，不证明实际生产工艺已执行。',
  '纳入核对的流程图工序号必须可读；无法辨认时保留无法判断。',
  '尺寸报告须对应图纸 Rev B。孔径 φ10.00±0.10 mm，3 件样本的每个实测值均须在 9.90–10.10 mm 内。',
]
const internalText = '尺寸报告须对应图纸 Rev B。孔径 φ10.00±0.05 mm，3 件样本的每个实测值均须在 9.95–10.05 mm 内。'
export function demoBasis(conflict = false): RequirementBasis {
  return {
    source: {id: 'p204-customer-v2', title: '北辰汽车 · P-204 审核规则', filename: customerSource, version: 'v2', origin: 'demo', state: 'ready'},
    clauses: texts.map((text, i) => ({id: `R${i + 1}`, title: ['工序一致性', '工序号可读性', 'Rev B 尺寸'][i], text, original: text, source: customerSource, location: ['第 2 页 §1', '第 2 页 §2', '第 3 页 §3'][i], applicability: 'pending', reason: '', checked: false})),
    conflict: {active: conflict, choice: 'pending', reason: ''}, confirmed: null,
  }
}
export function localBasis(filename: string, localFile?: LocalFileRef): RequirementBasis {
  return {source: {id: crypto.randomUUID(), title: filename.replace(/\.[^.]+$/, ''), filename, version: '', origin: 'local', state: 'unparsed', ...(localFile ? {localFile} : {})}, clauses: [], conflict: {active: false, choice: 'pending', reason: ''}, confirmed: null}
}
export function resolveBasisConflict(basis: RequirementBasis, choice: RequirementBasis['conflict']['choice']): RequirementBasis {
  return {...basis, confirmed: null, conflict: {...basis.conflict, choice}, clauses: basis.clauses.map(clause => clause.id !== 'R3' ? clause : {...clause,
    text: choice === 'internal' ? internalText : texts[2], original: choice === 'internal' ? internalText : texts[2],
    source: choice === 'internal' ? internalSource : customerSource, location: choice === 'internal' ? '第 1 页 §4' : '第 3 页 §3',
    checked: false, applicability: 'pending',
  })}
}
export function clauseSourceReady(clause: RequirementClause, basis?: RequirementBasis): boolean {
  if (![clause.title, clause.text, clause.source, clause.location].every(value => value.trim())) return false
  if (!basis?.server) return !clause.location.includes('待核对')
  const source = clause.provenance
  if (!source?.id || source.fileVersionId !== basis.server.material.file_version_id || source.location !== clause.location || source.excerpt !== clause.original || clause.source !== basis.server.material.original_filename) return false
  const catalog = basis.server.sources
  return catalog ? catalog.file_version_id === source.fileVersionId && catalog.units.some(unit => unit.id === source.id && unit.location === source.location && unit.excerpt === source.excerpt) : Boolean(basis.server.ruleId && basis.server.publishedContent)
}
export function clauseIssues(clause: RequirementClause, basis?: RequirementBasis): string[] {
  const issues: string[] = []
  if (!clauseSourceReady(clause, basis)) issues.push(`${clause.id}：补全条款与出处。`)
  if (!clause.checked) issues.push(`${clause.id}：核对条款与来源。`)
  if (clause.applicability === 'pending') issues.push(`${clause.id}：确认是否适用。`)
  if (clause.applicability === 'exclude' && !clause.reason.trim()) issues.push(`${clause.id}：说明不适用理由。`)
  return issues
}
export function basisIssues(basis: RequirementBasis | null | undefined): string[] {
  if (!basis) return ['请选择已有规则版本，或上传规则文件。']
  const issues: string[] = []
  if (!basis.source.title.trim() || !basis.source.version.trim()) issues.push('补全要求名称与版本。')
  if (['unparsed', 'failed'].includes(basis.source.state)) issues.push('规则文件尚无可核对条款；请人工录入，或更换文件。')
  if (!basis.clauses.length) issues.push('至少记录一条要求。')
  issues.push(...basis.clauses.flatMap(clause => clauseIssues(clause, basis)))
  if (!basis.clauses.some(clause => clause.applicability === 'apply')) issues.push('本次至少需要一条适用要求；不能用空范围开始审核。')
  if (basis.conflict.active && (basis.conflict.choice === 'pending' || !basis.conflict.reason.trim())) issues.push('确认公差冲突的依据，并记录理由。')
  return issues
}
function basisContent(basis: RequirementBasis): string {
  return JSON.stringify({source: basis.source, clauses: basis.clauses, conflict: basis.conflict, ...(basis.library ? {library: basis.library} : {})})
}
export function confirmBasis(basis: RequirementBasis, scope: string, by = '质量工程师（模拟身份）'): RequirementBasis {
  if (basisIssues(basis).length) return basis
  return {...basis, confirmed: {at: new Date().toISOString(), by, scope, content: basisContent(basis)}}
}
export function basisConfirmed(basis: RequirementBasis | null | undefined, scope: string): boolean {
  return Boolean(basis?.confirmed && basis.confirmed.scope === scope && basis.confirmed.content === basisContent(basis) && !basisIssues(basis).length)
}
export function isKnownDemoClause(clause: RequirementClause): boolean {
  const expected = demoBasis().clauses.find(item => item.id === clause.id)
  // A renamed heading does not change the clause; changed source/body cannot inherit its result.
  return Boolean(expected && clause.text === expected.text && clause.source === expected.source && clause.location === expected.location)
}
export function basisMatchesFixedReview(basis: RequirementBasis | null | undefined): boolean {
  return Boolean(basis?.confirmed && basis.source.origin === 'demo' && basis.clauses.length === 3 && basis.clauses.every(clause => clause.applicability === 'apply' && isKnownDemoClause(clause)))
}
export function isRequirementBasis(value: unknown): value is RequirementBasis {
  if (!isObject(value) || !isObject(value.source) || !isObject(value.conflict) || !Array.isArray(value.clauses)) return false
  const source = value.source
  if (!optionalLocalFile(source.localFile)) return false
  if (!['id', 'title', 'filename', 'version'].every(key => typeof source[key] === 'string') || !['demo', 'local'].includes(String(source.origin)) || !['ready', 'unparsed', 'failed', 'manual'].includes(String(source.state))) return false
  if (typeof value.conflict.active !== 'boolean' || !['pending', 'customer', 'internal'].includes(String(value.conflict.choice)) || typeof value.conflict.reason !== 'string') return false
  if (!value.clauses.every(clause => isObject(clause) && ['id', 'title', 'text', 'original', 'source', 'location', 'reason'].every(key => typeof clause[key] === 'string') && ['pending', 'apply', 'exclude'].includes(String(clause.applicability)) && typeof clause.checked === 'boolean')) return false
  if (new Set(value.clauses.map(clause => clause.id)).size !== value.clauses.length) return false
  if (value.library !== undefined && (!isObject(value.library) || !['collectionId', 'versionId', 'content'].every(key => typeof (value.library as Record<string, unknown>)[key] === 'string'))) return false
  const confirmed = value.confirmed
  return confirmed === null || isObject(confirmed) && ['at', 'by', 'scope', 'content'].every(key => typeof confirmed[key] === 'string')
}

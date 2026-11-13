// Requirement snapshots.
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
export function localBasis(filename: string, localFile?: LocalFileRef): RequirementBasis {
  return {source: {id: crypto.randomUUID(), title: filename.replace(/\.[^.]+$/, ''), filename, version: '', origin: 'local', state: 'unparsed', ...(localFile ? {localFile} : {})}, clauses: [], conflict: {active: false, choice: 'pending', reason: ''}, confirmed: null}
}
export function clauseSourceReady(clause: RequirementClause, basis?: RequirementBasis): boolean {
  if (![clause.title, clause.text, clause.source, clause.location].every(value => value.trim())) return false
  if (!basis?.server) return !clause.location.includes('待核对')
  const source = clause.provenance
  if (!source?.id || source.fileVersionId !== basis.server.material.file_version_id || source.location !== clause.location || source.excerpt !== clause.original || clause.source !== basis.server.material.original_filename) return false
  const catalog = basis.server.sources
  return catalog ? catalog.file_version_id === source.fileVersionId && catalog.units.some(unit => unit.id === source.id && unit.location === source.location && unit.excerpt === source.excerpt) : Boolean(basis.server.ruleId && basis.server.publishedContent)
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

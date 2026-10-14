// Prototype source versions. No Product API calls or file bytes are stored here.
import {demoBasis, isRequirementBasis, type RequirementBasis, type RequirementClause} from './requirements-state'
import {isObject} from './saved-draft'

export type RequirementScope = {customer: string; part: string; revision: string; allCustomers?: boolean; allParts?: boolean; allRevisions?: boolean}
export type RequirementVersion = {id: string; basis: RequirementBasis; scope: RequirementScope; reason: string; at: string; baseId: string | null}
export type RequirementDraft = Omit<RequirementVersion, 'at'> & {at?: undefined}
export type RequirementCollection = {id: string; versions: RequirementVersion[]; draft: RequirementDraft | null}
export type RequirementLibrary = {collections: RequirementCollection[]}
export type RequirementUse = {id: string; title: string; basis: RequirementBasis; submitted: boolean}

export function initialLibrary(): RequirementLibrary {
  const basis = demoBasis()
  return {collections: [{id: 'p204-customer', draft: null, versions: [{id: basis.source.id, basis: {...basis, clauses: basis.clauses.map(clause => ({...clause, checked: true}))}, scope: {customer: '北辰汽车', part: '支架 P-204', revision: 'B'}, reason: '已核对的示例来源。', at: '', baseId: null}]}]}
}
export const scopeLabel = (scope: RequirementScope) => `${scope.allCustomers ? '不限定客户' : scope.customer} · ${scope.allParts ? '不限定零件' : scope.part} · ${scope.allRevisions ? '不限定图纸修订' : `图纸 Rev ${scope.revision}`}`
export const scopeFields = [{key: 'customer', flag: 'allCustomers', label: '客户'}, {key: 'part', flag: 'allParts', label: '零件'}, {key: 'revision', flag: 'allRevisions', label: '图纸修订'}] as const
export const scopeMatches = (left: RequirementScope, right: RequirementScope) => scopeFields.every(({key, flag}) => right[flag] || left[key].trim().toLowerCase() === right[key].trim().toLowerCase())
export const clauseContent = (clause: RequirementClause) => JSON.stringify([clause.title, clause.text, clause.original, clause.source, clause.location])
export function sourceContent(basis: RequirementBasis) {
  return JSON.stringify([basis.source, basis.clauses.map(clauseContent), basis.conflict])
}
export function copyForCase(collection: RequirementCollection, version: RequirementVersion): RequirementBasis {
  const basis = structuredClone(version.basis)
  basis.source.id = version.id
  return {...basis, confirmed: null, library: {collectionId: collection.id, versionId: version.id, content: sourceContent(basis)}, clauses: basis.clauses.map(clause => ({...clause, checked: false, applicability: 'pending', reason: ''}))}
}
export function libraryDraftIssues(collection: RequirementCollection): string[] {
  const draft = collection.draft
  if (!draft) return []
  const issues: string[] = []
  if (scopeFields.some(({key, flag}) => !draft.scope[flag] && !draft.scope[key].trim())) issues.push('补全客户、零件与图纸修订范围，或明确不限定。')
  if (!draft.basis.source.title.trim() || !draft.basis.source.version.trim()) issues.push('补全要求名称与版本号。')
  if (collection.versions.some(version => version.basis.source.version.trim().toLowerCase() === draft.basis.source.version.trim().toLowerCase())) issues.push('此版本号已存在，请使用新的版本号。')
  if (!draft.reason.trim()) issues.push('记录来源说明或变更原因。')
  if (!draft.basis.clauses.length) issues.push('至少录入一条要求。')
  for (const clause of draft.basis.clauses) {
    if (![clause.title, clause.text, clause.source, clause.location].every(value => value.trim()) || clause.location.includes('待核对')) issues.push(`${clause.id}：补全条款与出处。`)
    if (!clause.checked) issues.push(`${clause.id}：核对来源内容。`)
  }
  return issues
}
export function isRequirementLibrary(value: unknown): value is RequirementLibrary {
  if (!isObject(value) || !Array.isArray(value.collections)) return false
  const validDraft = (draft: unknown): boolean => isObject(draft) && typeof draft.id === 'string' && typeof draft.reason === 'string' && (draft.baseId === null || typeof draft.baseId === 'string') && isRequirementBasis(draft.basis) && isObject(draft.scope) && scopeFields.every(({key, flag}) => typeof (draft.scope as Record<string, unknown>)[key] === 'string' && ((draft.scope as Record<string, unknown>)[flag] === undefined || typeof (draft.scope as Record<string, unknown>)[flag] === 'boolean'))
  return value.collections.every(collection => isObject(collection) && typeof collection.id === 'string' && (collection.draft === null || validDraft(collection.draft)) && Array.isArray(collection.versions) && collection.versions.every(version => validDraft(version) && isObject(version) && typeof version.at === 'string'))
}

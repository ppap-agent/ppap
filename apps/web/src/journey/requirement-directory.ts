import type {components} from '../generated/openapi.ts'
import type {RequirementCollection, RequirementDraft, RequirementScope, RequirementVersion} from './requirement-library-state.ts'

export type SourceReview = 'unchecked' | 'partial' | 'checked'
export type RequirementDirectoryVersion = {id: string; title: string; version: string; scope: RequirementScope; at: string; baseId: string | null; clauseCount: number; documentType: string; documentTypeAliases: string[]; suiteKey: string | null; sourceReview: SourceReview}

export function publishedRuleAction(version: {suiteKey: string | null; sourceReview: SourceReview}): string {
  if (!version.suiteKey) return '检查功能尚待完善'
  if (version.sourceReview === 'unchecked') return '核对出处'
  if (version.sourceReview === 'partial') return '继续确认'
  return '核对规则'
}

export function requirementHaystack(title: string, scopeText: string, documentType: string, aliases: readonly string[] = []) {
  return `${title} ${scopeText} ${documentType} ${(aliases ?? []).join(' ')}`.toLocaleLowerCase()
}

/** Keep Back on the previous page. Typing or clearing search replaces this address only. */
export function requirementQueryHref(current: string, query: string): string {
  const url = new URL(current)
  const value = query.slice(0, 200)
  if (value) url.searchParams.set('reqQuery', value)
  else url.searchParams.delete('reqQuery')
  return `${url.pathname}${url.search}${url.hash}`
}

export function clearsRequirementQueryOnEscape(query: string, key: string): boolean {
  return key === 'Escape' && query.length > 0
}

export function requirementSearchKeyAction(key: string, query: string): 'clear' | 'open' | 'none' {
  if (clearsRequirementQueryOnEscape(query, key)) return 'clear'
  if (key === 'Enter') return 'open'
  return 'none'
}
export type RequirementDirectoryCollection = {id: string; versions: RequirementDirectoryVersion[]; draft: RequirementDirectoryVersion | null}
export type ReadRequirementVersions = (ids: string[], signal: AbortSignal) => Promise<RequirementVersion[]>

function summarize(version: RequirementVersion | RequirementDraft): RequirementDirectoryVersion {
  const clauses = version.basis.clauses
  const checked = clauses.filter(clause => clause.checked).length
  const sourceReview: SourceReview = !clauses.length || checked === 0 ? 'unchecked' : checked === clauses.length ? 'checked' : 'partial'
  return {id: version.id, title: version.basis.source.title, version: version.basis.source.version, scope: version.scope, at: version.at ?? '', baseId: version.baseId, clauseCount: clauses.length, documentType: version.basis.server?.body.type ?? '', documentTypeAliases: [], suiteKey: version.basis.server?.body.suite_key ?? null, sourceReview}
}

export function groupRequirementDirectory(entries: components['schemas']['RuleLibraryEntry'][]): RequirementDirectoryCollection[] {
  const versions = entries.map(row => ({id: row.id, title: row.title, version: row.version, scope: row.scope, at: row.created_at, baseId: row.predecessor_id, clauseCount: row.clause_count, documentType: row.document_type ?? '', documentTypeAliases: row.document_type_aliases ?? [], suiteKey: row.suite_key ?? null, sourceReview: row.source_review ?? 'unchecked'}))
  const byId = new Map(versions.map(version => [version.id, version]))
  const groups = new Map<string, RequirementDirectoryCollection>()
  for (const version of versions) {
    let root = version
    const seen = new Set<string>()
    while (root.baseId && byId.has(root.baseId) && !seen.has(root.baseId)) {seen.add(root.id); root = byId.get(root.baseId)!}
    const group = groups.get(root.id) ?? {id: root.id, versions: [], draft: null}
    group.versions.push(version); groups.set(root.id, group)
  }
  return [...groups.values()].map(group => ({...group, versions: group.versions.sort((a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id))}))
}

export function requirementDirectory(collections: RequirementCollection[], remote?: RequirementDirectoryCollection[]): RequirementDirectoryCollection[] {
  const groups = new Map((remote ?? []).map(group => [group.id, {...group, versions: [...group.versions]}]))
  for (const collection of collections) {
    const group = groups.get(collection.id) ?? {id: collection.id, versions: [], draft: null}
    group.draft = collection.draft ? summarize(collection.draft) : null
    for (const version of collection.versions) if (!group.versions.some(row => row.id === version.id)) group.versions.push(summarize(version))
    group.versions.sort((a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id))
    groups.set(group.id, group)
  }
  return [...groups.values()]
}


export function publishedLibraryAfterRead<T>(failed: boolean, versions: T[]): {versions: T[]; groups: RequirementDirectoryCollection[]} {
  if (failed) return {versions: [], groups: []}
  return {versions, groups: groupRequirementDirectory(versions as Parameters<typeof groupRequirementDirectory>[0])}
}

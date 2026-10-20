import type {components} from '../generated/openapi.ts'
import type {RequirementCollection, RequirementDraft, RequirementScope, RequirementVersion} from './requirement-library-state.ts'

export type RequirementDirectoryVersion = {id: string; title: string; version: string; scope: RequirementScope; at: string; baseId: string | null; clauseCount: number}
export type RequirementDirectoryCollection = {id: string; versions: RequirementDirectoryVersion[]; draft: RequirementDirectoryVersion | null}
export type ReadRequirementVersions = (ids: string[], signal: AbortSignal) => Promise<RequirementVersion[]>

function summarize(version: RequirementVersion | RequirementDraft): RequirementDirectoryVersion {
  return {id: version.id, title: version.basis.source.title, version: version.basis.source.version, scope: version.scope, at: version.at ?? '', baseId: version.baseId, clauseCount: version.basis.clauses.length}
}

export function groupRequirementDirectory(entries: components['schemas']['RuleLibraryEntry'][]): RequirementDirectoryCollection[] {
  const versions = entries.map(row => ({id: row.id, title: row.title, version: row.version, scope: row.scope, at: row.created_at, baseId: row.predecessor_id, clauseCount: row.clause_count}))
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

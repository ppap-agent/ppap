import type {RuleSources} from '../api'
import {clauseSourceReady, type RequirementBasis, type RequirementClause} from './requirements-state.ts'

const normalized = (text: string) => text.replace(/\s+/g, ' ').trim()
export function citeRequirementSource(clause: RequirementClause, unit: RuleSources['units'][number], basis: RequirementBasis): RequirementClause {
  return {...clause, source: basis.server!.material.original_filename, location: unit.location, original: unit.excerpt,
    provenance: {...unit, fileVersionId: basis.server!.material.file_version_id}, checked: clause.checked && normalized(clause.original) === normalized(unit.excerpt)}
}
export function restoreRequirementSources(basis: RequirementBasis, sources: RuleSources): RequirementBasis {
  if (basis.server?.material.file_version_id !== sources.file_version_id) return basis
  let changed = false
  const clauses = basis.clauses.map(clause => {
    // Only upgrade the old generated placeholder. Never overwrite manual edits.
    if (clause.provenance || clause.location !== `条款 ${clause.id}（原件位置待确认）`) return clause
    const matches = sources.units.filter(unit => normalized(unit.excerpt) === normalized(clause.original))
    if (matches.length !== 1) return clause
    changed = true
    return citeRequirementSource(clause, matches[0], basis)
  })
  return changed ? {...basis, clauses} : basis
}
export const verifiedClauseCount = (basis: RequirementBasis) => basis.clauses.filter(clause => clause.checked && clauseSourceReady(clause, basis)).length

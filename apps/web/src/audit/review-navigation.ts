import type {components} from '../generated/openapi'
import {clauseObservation} from './result-projection.ts'
type Check = components['schemas']['AuditClauseEvaluation']
export const reviewable = (check: Check) => check.finding?.status !== 'NA'
export function reviewed(check: Check) {
  const review = check.review ?? check.finding?.review
  return Boolean(reviewable(check) && review?.rationale.trim() && ['confirm', 'disagree', 'unknown', 'manual-satisfied', 'manual-unsatisfied'].includes(review.judgment)
    && (!review.judgment.startsWith('manual-') || review.citations?.length && review.citations.every(cite => cite.file_version_id && cite.location.trim() && cite.excerpt.trim())))
}
export const needsAttention = (check: Check) => !(check.execution === 'completed' && check.coverage === 'executed' && ['PASS', 'NA'].includes(check.finding?.status || ''))
export function filterChecks(checks: Check[], query: string, filter: string) {
  return checks.filter(row => (row.rule_item.id + ' ' + row.rule_item.category + ' ' + row.rule_item.rule_to_check + ' ' + clauseObservation(row)).toLowerCase().includes(query.toLowerCase()) && (
    filter === 'all' || filter === 'suggested' && Boolean(row.finding?.review_hint) || filter === 'unreviewed' && reviewable(row) && !reviewed(row)
    || filter === 'attention' && needsAttention(row)
    || filter === 'error' && (row.execution === 'failed' || row.finding?.status === 'ERR')
    || filter === 'uncovered' && row.execution === 'completed' && row.coverage !== 'executed' && row.finding?.status !== 'ERR'
    || row.execution === 'completed' && row.coverage === 'executed' && row.finding?.status === ({ng: 'NG', unable: 'UNABLE', pass: 'PASS', na: 'NA'} as Record<string, string>)[filter]
  ))
}
export function nextUnreviewed(checks: Check[], current: string) {
  const index = checks.findIndex(row => row.rule_item.id === current)
  return [...checks.slice(index + 1), ...checks.slice(0, Math.max(0, index))].find(row => reviewable(row) && !reviewed(row))
}

import type {components} from './generated/openapi'

export type ReviewRound = components['schemas']['Round']
export type ReviewRequest = import('./api').ReviewRequest

export function reviewOf(round: ReviewRound, key: string) {
  return round.findings.find(row => row.item_key === key)?.review ?? round.coverage.rule_items?.find(row => `coverage:${row.file_version_id}:${row.id}` === key)?.review ?? null
}

export function reviewMatches(round: ReviewRound, request: ReviewRequest) {
  const review = reviewOf(round, request.item_key)
  return Boolean(review && review.version === request.version + 1 && review.judgment === request.judgment && review.rationale === request.rationale && review.next_step === request.next_step && (review.citations || []).length === (request.citations || []).length && (review.citations || []).every((cite, index) => cite.file_version_id === request.citations?.[index]?.file_version_id && cite.location === request.citations?.[index]?.location && cite.excerpt === request.citations?.[index]?.excerpt))
}

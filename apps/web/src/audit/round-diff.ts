import type {components} from '../generated/openapi'

type Check = components['schemas']['AuditClauseEvaluation']
export type RoundDiff = {added: Check[]; resolved: Check[]; persisted: Check[]; uncomparable: number}

const settled = (check: Check, status: string) => check.execution === 'completed' && check.coverage === 'executed' && check.finding?.status === status

/** Only explicit PASS/NG results on the same rule text count as a change; anything unknown stays uncomparable. */
export function diffRounds(current: Check[], previous: Check[]): RoundDiff {
  const earlier = new Map(previous.map(check => [check.rule_item.id, check]))
  const diff: RoundDiff = {added: [], resolved: [], persisted: [], uncomparable: 0}
  for (const check of current) {
    const before = earlier.get(check.rule_item.id)
    const same = before?.rule_item.rule_to_check.trim() === check.rule_item.rule_to_check.trim()
    if (same && settled(check, 'NG') && settled(before!, 'PASS')) diff.added.push(check)
    else if (same && settled(check, 'PASS') && settled(before!, 'NG')) diff.resolved.push(check)
    else if (same && settled(check, 'NG') && settled(before!, 'NG')) diff.persisted.push(check)
    else if (!(same && settled(check, 'PASS') && settled(before!, 'PASS')) && !(same && settled(check, 'NA') && settled(before!, 'NA'))) diff.uncomparable++
  }
  return diff
}

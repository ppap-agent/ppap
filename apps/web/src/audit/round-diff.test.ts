import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi'
import {diffRounds} from './round-diff.ts'

type Check = components['schemas']['AuditClauseEvaluation']
const check = (id: string, status: string | null, text = '要求', execution = 'completed', coverage = 'executed') => ({rule_item: {id, category: id, rule_to_check: text}, execution, coverage, finding: status ? {status} : null}) as unknown as Check
const ids = (rows: Check[]) => rows.map(row => row.rule_item.id)

test('separates new, resolved and persisting issues on the same rule text', () => {
  const diff = diffRounds([check('a', 'NG'), check('b', 'PASS'), check('c', 'NG'), check('d', 'PASS')], [check('a', 'PASS'), check('b', 'NG'), check('c', 'NG'), check('d', 'PASS')])
  assert.deepEqual([ids(diff.added), ids(diff.resolved), ids(diff.persisted), diff.uncomparable], [['a'], ['b'], ['c'], 0])
})

test('never reads an unknown or changed item as a change in result', () => {
  const diff = diffRounds([check('unable', 'PASS'), check('now-unable', 'UNABLE'), check('rewritten', 'NG', '新要求'), check('added', 'NG'), check('failed', 'PASS', '要求', 'failed'), check('uncovered', 'NG', '要求', 'completed', 'unsupported')], [check('unable', 'UNABLE'), check('now-unable', 'NG'), check('rewritten', 'PASS'), check('failed', 'NG'), check('uncovered', 'PASS')])
  assert.deepEqual([diff.added, diff.resolved, diff.persisted, diff.uncomparable], [[], [], [], 6])
})

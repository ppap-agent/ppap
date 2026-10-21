import assert from 'node:assert/strict'
import test from 'node:test'
import {filterChecks, nextUnreviewed, reviewed} from './review-navigation.ts'
import type {components} from '../generated/openapi.ts'

type Check = components['schemas']['AuditClauseEvaluation']
const check = (id: string, status = 'NG', judgment?: string) => ({rule_item: {id, category: id, rule_to_check: '要求'}, execution: 'completed', coverage: 'executed', finding: {status}, review: judgment ? {judgment, rationale: '已核对'} : null}) as Check
test('filters retain rule order and distinguish machine outcome from optional review', () => {
  const rows = [check('10', 'PASS'), check('2', 'NG', 'confirm'), check('3', 'UNABLE'), check('4', 'NG', 'pending')]
  assert.deepEqual(filterChecks(rows, '', 'ng').map(row => row.rule_item.id), ['2', '4'])
  assert.deepEqual(filterChecks(rows, '', 'unable').map(row => row.rule_item.id), ['3'])
  assert.deepEqual(filterChecks(rows, '', 'unreviewed').map(row => row.rule_item.id), ['10', '3', '4'])
  assert.equal(reviewed(rows[3]), false)
})
test('next unreviewed follows rule order, wraps, and excludes the just-saved clause', () => {
  const rows = [check('10', 'PASS'), check('2', 'NG', 'confirm'), check('3', 'NG')]
  assert.equal(nextUnreviewed(rows, '10')?.rule_item.id, '3')
  assert.equal(nextUnreviewed(rows, '3')?.rule_item.id, '10')
  assert.equal(nextUnreviewed([check('1')], '1'), undefined)
})

import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi'
import {ruleCoverage} from './rule-coverage-state.ts'

type Entry = components['schemas']['RuleLibraryEntry']
const entry = (id: string, customer: string, document_type: string, extra: Partial<Entry> & {allCustomers?: boolean} = {}) => ({id, title: id, version: 'v1', reason: '', created_at: '', predecessor_id: null, clause_count: 1, document_type, scope: {customer, part: '', revision: '', allCustomers: extra.allCustomers ?? false, allParts: true, allRevisions: true}, ...extra}) as Entry

test('counts a customer rule and all-customer rules against the full type list', () => {
  const [uaes, nio] = ruleCoverage([entry('a', 'UAES', 'PFMEA'), entry('b', '蔚来', 'PSW'), entry('all', '', '控制计划', {allCustomers: true})], ['PFMEA', 'PSW', '控制计划']).sort(row => row.customer === 'UAES' ? -1 : 1)
  assert.deepEqual([uaes.customer, uaes.covered, uaes.total], ['UAES', 2, 3])
  assert.deepEqual(uaes.types.find(row => row.type === 'PSW')?.rules, [])
  assert.deepEqual([nio.customer, nio.covered], ['蔚来', 2])
})

test('merges customer spelling and counts only the newest version', () => {
  const rows = ruleCoverage([entry('old', 'UAES', 'PSW'), entry('new', 'uaes', 'PFMEA', {predecessor_id: 'old'})], ['PFMEA', 'PSW'])
  assert.equal(rows.length, 1)
  assert.deepEqual(rows[0].types, [{type: 'PFMEA', rules: ['new']}, {type: 'PSW', rules: []}])
})

test('shows all-customer rules on their own when no customer is named', () => {
  assert.deepEqual(ruleCoverage([entry('all', '', 'PSW', {allCustomers: true})], ['PSW']).map(row => [row.customer, row.covered]), [['全部客户', 1]])
  assert.deepEqual(ruleCoverage([], ['PSW']), [])
})

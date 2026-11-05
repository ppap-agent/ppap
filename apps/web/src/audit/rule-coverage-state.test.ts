import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi'
import {ruleCoverage} from './rule-coverage-state.ts'

type Rule = components['schemas']['Rule']
const rule = (id: string, customers: string[], type: string, extra: Partial<Rule> = {}) => ({id, title: id, kind: 'engine', created_at: '', predecessor_id: null, customer_scope: customers.length ? 'restricted' : 'unconfirmed', type, customers, ...extra}) as Rule
const standard = ['PFMEA', 'PSW', '控制计划', 'DFMEA']

test('compares customers on standard types that have a rule and keeps customer-only types apart', () => {
  const [li, uaes] = ruleCoverage([rule('l1', ['理想'], 'PFMEA'), rule('l2', ['理想'], 'PSW'), rule('u1', ['UAES'], 'PFMEA'), rule('u2', ['UAES'], 'E07', {title: '生产件样品照片审核规则'})], standard).sort(row => row.customer === '理想' ? -1 : 1)
  assert.deepEqual([li.customer, li.covered, li.total, li.types.map(row => row.type)], ['理想', 2, 2, ['PFMEA', 'PSW']])
  assert.deepEqual([uaes.customer, uaes.covered, uaes.total], ['UAES', 2, 3])
  assert.deepEqual(uaes.types.at(-1), {type: 'E07', label: '生产件样品照片', rules: ['生产件样品照片审核规则']})
  assert.deepEqual(uaes.types.find(row => row.type === 'PSW')?.rules, [])
})

test('merges customer spelling and counts only the newest version', () => {
  const rows = ruleCoverage([rule('old', ['UAES'], 'PSW'), rule('new', ['uaes'], 'PFMEA', {predecessor_id: 'old'})], standard)
  assert.equal(rows.length, 1)
  assert.deepEqual(rows[0].types, [{type: 'PFMEA', label: 'PFMEA', rules: ['new']}])
})

test('applies confirmed all-customer rules to everyone and ignores unconfirmed scope', () => {
  const all = rule('all', [], 'PSW', {customer_scope: 'all'})
  assert.deepEqual(ruleCoverage([all, rule('a', ['A'], 'PFMEA')], standard)[0].covered, 2)
  assert.deepEqual(ruleCoverage([all], standard).map(row => [row.customer, row.covered]), [['全部客户', 1]])
  assert.deepEqual(ruleCoverage([rule('x', [], 'PSW')], standard), [])
})

test('lists a rule title once when a built-in and an uploaded rule share it', () => {
  const rows = ruleCoverage([rule('built-in', ['UAES'], 'PSW', {title: 'PSW 规则'}), rule('uploaded', ['UAES'], 'PSW', {title: 'PSW 规则'})], standard)
  assert.deepEqual(rows[0].types[0].rules, ['PSW 规则'])
})

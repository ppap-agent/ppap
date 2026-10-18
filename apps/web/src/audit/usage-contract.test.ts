import assert from 'node:assert/strict'
import test from 'node:test'
import {validUsagePage, type UsageEntry, type UsagePage} from './usage-contract.ts'

const known = (value: number) => ({value, reason: null})
const query = {range: '30d' as const, search: '', page: 1, page_size: 10 as const}
const entry: UsageEntry = {usage_id: 'business-1', occurred_at: '2026-10-10T10:00:00+08:00', debited_at: '2026-10-10T10:00:00+08:00', filename: 'a.xlsx', audit_sequence: 1, initiator: 'Li', points: 500, allocation: {free_points: 200, paid_points: 300, reason: null}, orders: [{order_id: 'provider-order', confirmed_at: '2026-10-10T10:00:00+08:00'}], order_reason: null, settlement: {state: 'charged', reason: null}, reference: {task_id: 'task-1', round_id: 'round-1', file_version_id: 'version-1', result_attempt: 1}, document_id: 'document-1', audit_access: {state: 'allowed', reason: null}}
const page: UsagePage = {organization_id: 'org-1', organization_name: 'A', query, summary: {month: '2026-10', timezone: 'Asia/Shanghai', as_of: '2026-10-16T10:00:00+08:00', consumption_count: known(12), charged_points: known(4500), pending_points: known(1500), free_remaining: known(300), free_total: known(1000)}, entries: [entry], total: 1, from_date: '2026-09-17', through_date: '2026-10-16', notices: []}

test('a paid debit needs its actual time and order, not only a reporting acknowledgement', () => {
  const charged = {...entry, debited_at: entry.orders[0].confirmed_at}
  assert.equal(validUsagePage({...page, entries: [charged]}, query), true)
  for (const change of [{debited_at: null}, {debited_at: '2026-10-09T10:00:00+08:00'}, {debited_at: '2026-10-17T10:00:00+08:00'}, {orders: []}, {debited_at: '2026-10-10T10:00:00'}, {settlement: {state: 'unknown' as const, reason: 'Awaiting reconciliation'}}]) {
    assert.equal(validUsagePage({...page, entries: [{...charged, ...change}]}, query), false)
  }
})

test('technical result attempts cannot become separate per-file business charges', () => {
  assert.equal(validUsagePage(page, query, 'org-1'), true)
  const retry = {...entry, usage_id: 'business-2', reference: {...entry.reference, result_attempt: 2}}
  assert.equal(validUsagePage({...page, entries: [entry, retry], total: 2}, query, 'org-1'), false)
})

test('one file audit has the confirmed 500-point business obligation', () => {
  assert.equal(validUsagePage({...page, entries: [{...entry, points: 1000, allocation: {...entry.allocation, paid_points: 800}}]} as unknown as UsagePage, query, 'org-1'), false)
})

test('unmapped history keeps its fee without inventing an audit sequence or hiding older aggregate fees', () => {
  const unmapped = {...entry, audit_sequence: null, document_id: null, audit_access: {state: 'denied' as const, reason: 'History is not authorized'}}
  const unattributed = {slice_count: 1, points: 1200, reason: 'An older aggregate fee has no file allocation'}
  assert.equal(validUsagePage({...page, entries: [unmapped], unattributed}, query), true)
  assert.equal(validUsagePage({...page, unattributed: {...unattributed, reason: null}}, query), false)
  assert.equal(validUsagePage({...page, unattributed: {...unattributed, points: -1}}, query), false)
})

test('settlement certainty cannot contradict the allocation or declared date window', () => {
  assert.equal(validUsagePage({...page, entries: [{...entry, settlement: {state: 'trial', reason: null}, allocation: {free_points: 0, paid_points: 500, reason: null}}]}, query), false)
  assert.equal(validUsagePage({...page, entries: [{...entry, occurred_at: '2026-09-16T10:00:00+08:00'}]}, query), false)
})

test('usage projections bind organization, query and natural month and keep unknown distinct from zero', () => {
  assert.equal(validUsagePage({...page, summary: {...page.summary, charged_points: {value: null, reason: 'Awaiting receipt'}}}, query, 'org-1'), true)
  assert.equal(validUsagePage({...page, summary: {...page.summary, charged_points: known(0)}}, query, 'org-1'), true)
  for (const change of [{organization_id: 'other-org'}, {query: {...query, search: 'other'}}, {summary: {...page.summary, month: '2026-09'}}, {summary: {...page.summary, charged_points: {value: null, reason: null}}}, {summary: {...page.summary, free_remaining: known(1001)}}, {entries: [{...entry, orders: [{...entry.orders[0], order_id: ''}]}]}, {entries: [{...entry, allocation: {...entry.allocation, paid_points: 301}}]}, {entries: [{...entry, reference: {...entry.reference, file_version_id: ''}}]}]) assert.equal(validUsagePage({...page, ...change}, query, 'org-1'), false)
})

import assert from 'node:assert/strict'
import test from 'node:test'
import {beijingTime, chargeText, pointsText, receiptOf} from './receipts-contract.ts'

test('a round charge says its points and how they stand; nothing charged is said so, never as zero points', () => {
  assert.equal(chargeText({points: 500, state: 'pending'}), '本轮扣点 500 点 · 待扣费')
  assert.equal(chargeText({points: 1500, state: 'unknown'}), '本轮扣点 1,500 点 · 待确认')
  assert.equal(chargeText({points: 500, state: 'trial'}), '本轮扣点 500 点 · 免费额度已扣')
  assert.equal(chargeText({points: 0, state: 'none'}), '本轮未扣点')
  assert.equal(pointsText({points: 0, state: 'none'}), '未扣点')
  assert.equal(pointsText({points: 1000, state: 'charged'}), '1,000 点 · 已扣费')
})

test('receipt times are Beijing time, whatever zone the browser is in', () => {
  assert.equal(beijingTime('2026-11-21T16:05:09+00:00'), '2026-11-22 00:05:09')
  assert.equal(beijingTime('2026-11-22T08:30:00+08:00'), '2026-11-22 08:30:00')
})

test('only an action id in the response header is a receipt', () => {
  assert.equal(receiptOf('0F9D6C2E-1D3B-4A51-9A0E-6A2B7C8D9E10'), '0f9d6c2e-1d3b-4a51-9a0e-6a2b7c8d9e10')
  for (const value of [null, '', 'R-20261122-0007', '../receipts']) assert.equal(receiptOf(value), null)
})

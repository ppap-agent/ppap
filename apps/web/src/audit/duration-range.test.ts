import assert from 'node:assert/strict'
import test from 'node:test'

import {durationRange} from './duration-range.ts'

test('the interval scales with the selected file count', () => {
  assert.equal(durationRange({low: 60, high: 120}, 3), '约 3～6 分钟')
  assert.equal(durationRange({low: 60, high: 120}, 1), '约 1～2 分钟')
  assert.equal(durationRange({low: 50, high: 70}, 2), '约 2～3 分钟')
})

test('no estimate, no files or a malformed interval shows nothing', () => {
  assert.equal(durationRange(null, 3), null)
  assert.equal(durationRange(undefined, 3), null)
  assert.equal(durationRange({low: 60, high: 120}, 0), null)
  assert.equal(durationRange({low: 120, high: 60}, 1), null)
  assert.equal(durationRange({low: Number.NaN, high: 60}, 1), null)
})

test('short and long totals use the matching unit', () => {
  assert.equal(durationRange({low: 10, high: 20}, 2), '不到 1 分钟')
  assert.equal(durationRange({low: 40, high: 61}, 1), '约 1～2 分钟')
  assert.equal(durationRange({low: 60, high: 60}, 5), '约 5 分钟')
  assert.equal(durationRange({low: 600, high: 1200}, 6), '约 1～2 小时')
  assert.equal(durationRange({low: 1800, high: 2700}, 4), '约 2～3 小时')
})

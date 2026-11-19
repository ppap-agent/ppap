import assert from 'node:assert/strict'
import test from 'node:test'
import {chapterAt, outlineChapters, precheckReportChapters, searchOrder, textIncludes} from './report-chapters.ts'

test('the P3 contents are the cover and the ten chapters in the report order', () => {
  assert.equal(precheckReportChapters.length, 11)
  assert.equal(precheckReportChapters[0], '封面')
  assert.deepEqual(precheckReportChapters.slice(1).map(title => title.split('、')[0]), ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十'])
  assert.equal(precheckReportChapters[4], '四、阻断项明细')
  assert.equal(precheckReportChapters[9], '九、附录 · 检查项全量结果')
})

test('outline entries become chapters with their pages; unresolvable or empty entries are left out', async () => {
  const pages: Record<string, number> = {cover: 1, one: 3, two: 5}
  const outline = [{title: '封面', dest: 'cover'}, {title: '一、结论摘要', dest: 'one'}, {title: '坏', dest: 'missing'}, {title: '空', dest: null}, {title: '二、提交范围与文件集', dest: 'two'}]
  const chapters = await outlineChapters(outline, async dest => {const page = pages[dest as string]; if (!page) throw new Error('missing'); return page})
  assert.deepEqual(chapters, [{title: '封面', page: 1}, {title: '一、结论摘要', page: 3}, {title: '二、提交范围与文件集', page: 5}])
  // An older report without an outline has no chapter list.
  assert.deepEqual(await outlineChapters(null, async () => 1), [])
})

test('the chapter being read is the last one starting on or before the page', () => {
  const chapters = [{title: '封面', page: 1}, {title: '一', page: 3}, {title: '二', page: 5}]
  assert.equal(chapterAt(chapters, 1), 0)
  assert.equal(chapterAt(chapters, 2), 0)
  assert.equal(chapterAt(chapters, 4), 1)
  assert.equal(chapterAt(chapters, 9), 2)
  assert.equal(chapterAt([], 3), -1)
})

test('search ignores layout line breaks and starts after the current page', () => {
  assert.ok(textIncludes('冻结审核\n报告', '冻结审核报告'))
  assert.ok(!textIncludes('冻结审核报告', '  '))
  assert.deepEqual(searchOrder(2, 4), [3, 4, 1, 2])
  assert.deepEqual(searchOrder(4, 4), [1, 2, 3, 4])
})

import assert from 'node:assert/strict'
import test from 'node:test'
import {highlightParts} from './highlight.ts'

test('marks every case-insensitive hit and keeps the original casing', () => {
  assert.deepEqual(highlightParts('PSW 与 psw 表', 'psw'), [{text: 'PSW', hit: true}, {text: ' 与 ', hit: false}, {text: 'psw', hit: true}, {text: ' 表', hit: false}])
})

test('treats search terms literally and ignores a blank query', () => {
  assert.deepEqual(highlightParts('1.2 (a)', '(a)'), [{text: '1.2 ', hit: false}, {text: '(a)', hit: true}])
  assert.deepEqual(highlightParts('控制计划', '  '), [{text: '控制计划', hit: false}])
})

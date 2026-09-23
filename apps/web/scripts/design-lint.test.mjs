import assert from 'node:assert/strict'
import test from 'node:test'
import {lintSource, lintTree} from './design-lint.mjs'

test('feature violations are named and a vocabulary call is clean', () => {
  assert.deepEqual(lintSource('<div className="x" />'), ['no-classname', 'no-raw-layout'])
  assert.ok(lintSource('<Box style={{color: "red"}} />').includes('no-inline-style'))
  assert.ok(lintSource('<button type="button">保存</button>').includes('no-raw-controls'))
  assert.ok(lintSource("import './panel.css'").includes('no-direct-styling-backend'))
  assert.ok(lintSource("import stylex from '@stylexjs/stylex'").includes('no-direct-styling-backend'))
  assert.ok(lintSource('const ink = "#235fc7"').includes('no-design-literals'))
  assert.ok(lintSource('const gap = "12px"').includes('no-design-literals'))
  assert.deepEqual(lintSource('<Button type="button" variant="primary">保存</Button>'), [])
  assert.deepEqual(lintSource("const href = '#report-' + key"), [])
})

test('the product tree has one styling path outside the backend', () => {
  assert.deepEqual(lintTree(), [])
})

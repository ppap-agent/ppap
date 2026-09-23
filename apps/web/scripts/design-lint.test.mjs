import assert from 'node:assert/strict'
import {mkdtempSync, mkdirSync, rmSync, writeFileSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import test from 'node:test'
import {lintSource, lintStylesheet, lintTree} from './design-lint.mjs'

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

test('object style and raw icon size are rejected before they reach the DOM', () => {
  const spread = "const extra = {title: '说明', style: {opacity: 0.2}}\n<Button {...extra}>提交</Button>"
  assert.ok(lintSource(spread).includes('no-inline-style'))
  assert.ok(lintSource('<Search size={37} />').includes('no-raw-icon-size'))
  assert.ok(lintSource("import {Box} from './ui/system/layout'").includes('no-direct-styling-backend'))
})

test('system css must reference tokens', () => {
  const css = '[data-ui="button"] { background: #ff00ff; padding: 17px; border-radius: 19px; color: var(--made-up); }'
  const failures = lintStylesheet(css)
  assert.ok(failures.some(item => item.startsWith('background')))
  assert.ok(failures.some(item => item.startsWith('padding')))
  assert.ok(failures.some(item => item.startsWith('border-radius')))
  assert.ok(failures.some(item => item.includes('unknown token --made-up')))
})

test('system css tokens must match the property intent domain', () => {
  const css = '[data-ui="bad"] { font-size: var(--space-lg); padding: var(--size-sidebar); border-radius: var(--font-size-sm); }'
  const failures = lintStylesheet(css)
  assert.ok(failures.some(item => item.includes('font-size') && item.includes('--space-lg')))
  assert.ok(failures.some(item => item.includes('padding') && item.includes('--size-sidebar')))
  assert.ok(failures.some(item => item.includes('border-radius') && item.includes('--font-size-sm')))
})

test('a system stylesheet or a direct system import fails the tree contract', () => {
  const root = mkdtempSync(join(tmpdir(), 'design-lint-'))
  mkdirSync(join(root, 'src/ui/system'), {recursive: true})
  mkdirSync(join(root, 'src/feature'), {recursive: true})
  writeFileSync(join(root, 'src/ui/system/bad.css'), '[data-ui="button"] { background: #ff00ff; padding: 17px; border-radius: 19px; }\n')
  writeFileSync(join(root, 'src/ui/system/bad.tsx'), "export const bad = {fontSize: 17, padding: 12, margin: 0}\n")
  writeFileSync(join(root, 'src/feature/Card.tsx'), "import {Box} from '../ui/system/layout'\nexport function Card() { return <Box>x</Box> }\n")
  try {
    const failures = lintTree(root)
    assert.ok(failures.some(item => item.includes('bad.css') && item.includes('background')))
    assert.ok(failures.some(item => item.includes('bad.tsx') && item.includes('raw numeric design value 17')))
    assert.ok(failures.some(item => item.includes('bad.tsx') && item.includes('raw numeric design value 12')))
    assert.ok(!failures.some(item => item.includes('bad.tsx') && item.includes('raw numeric design value 0')))
    assert.ok(failures.some(item => item.includes('Card.tsx') && item.includes('no-direct-styling-backend')))
  } finally {
    rmSync(root, {recursive: true, force: true})
  }
})

test('the product tree has one styling path and the system layer stays on tokens', () => {
  assert.deepEqual(lintTree(), [])
})

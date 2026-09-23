import {readdirSync, readFileSync, statSync} from 'node:fs'
import {dirname, join} from 'node:path'
import {fileURLToPath} from 'node:url'

const srcRoot = join(dirname(fileURLToPath(import.meta.url)), '../src')

/** Stylesheets that still have consumers. #95 deletes these; any other feature CSS fails. */
export const legacyStylesheets = new Set(['style.css', 'ui/foundation.css'])

const rawLayout = ['div', 'section', 'main', 'header', 'footer', 'nav', 'article', 'aside', 'form', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'ul', 'ol', 'li', 'fieldset', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'span', 'dl', 'dt', 'dd', 'pre', 'iframe', 'code', 'strong', 'em', 'small']
const rawControls = ['button', 'input', 'select', 'textarea', 'a', 'label']

const layoutPattern = new RegExp(`</?(?:${rawLayout.join('|')})\\b`)
const controlPattern = new RegExp(`</?(?:${rawControls.join('|')})\\b`)

export function lintSource(source) {
  const violations = []
  if (/\bclassName\s*=/.test(source)) violations.push('no-classname')
  if (/\bstyle\s*=\s*\{/.test(source)) violations.push('no-inline-style')
  if (layoutPattern.test(source)) violations.push('no-raw-layout')
  if (controlPattern.test(source)) violations.push('no-raw-controls')
  if (/from\s+['"](?:stylex|@stylexjs\/stylex|styled-components|@emotion\/)/.test(source) || /import\s+['"][^'"]+\.css['"]/.test(source) || /var\(--/.test(source) || /--pc-/.test(source)) {
    violations.push('no-direct-styling-backend')
  }
  if (/['"]#[0-9a-fA-F]{3,8}['"]/.test(source) || /\brgba?\(/.test(source) || /\bhsla?\(/.test(source) || /\d+px\b/.test(source)) {
    violations.push('no-design-literals')
  }
  return violations
}

export function lintTree(root = srcRoot) {
  const failures = []
  function walk(directory, relative = '') {
    for (const name of readdirSync(directory)) {
      const path = join(directory, name)
      const next = relative ? `${relative}/${name}` : name
      if (statSync(path).isDirectory()) {
        if (next === 'ui/system') continue
        walk(path, next)
        continue
      }
      if (name.endsWith('.css')) {
        if (!legacyStylesheets.has(next)) failures.push(`${next}: no-feature-css`)
        continue
      }
      if (next.startsWith('ui/') || !/\.tsx?$/.test(name) || name.endsWith('.test.ts')) continue
      for (const rule of lintSource(readFileSync(path, 'utf8'))) failures.push(`${next}: ${rule}`)
    }
  }
  walk(root)
  return failures
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const failures = lintTree()
  if (failures.length) {
    console.error(failures.join('\n'))
    process.exit(1)
  }
}

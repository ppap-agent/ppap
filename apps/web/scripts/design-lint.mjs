import {readdirSync, readFileSync, statSync} from 'node:fs'
import {dirname, join} from 'node:path'
import {fileURLToPath} from 'node:url'

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

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

export function lintTree(root = webRoot) {
  const failures = []
  function walk(directory, relative = '') {
    for (const name of readdirSync(directory)) {
      const path = join(directory, name)
      const next = relative ? `${relative}/${name}` : name
      if (name === 'node_modules' || name === 'dist') continue
      if (statSync(path).isDirectory()) {
        walk(path, next)
        continue
      }
      if (!/\.(css|tsx?|html)$/.test(name)) continue
      const text = readFileSync(path, 'utf8')
      const backend = next.startsWith('src/ui/system/')
      if (text.includes('--pc-') || text.includes('--ui-')) failures.push(`${next}: no-legacy-alias`)
      if (name.endsWith('.css') && !backend) failures.push(`${next}: no-feature-css`)
      if (backend || !next.startsWith('src/') || next.startsWith('src/ui/') || name.endsWith('.test.ts')) continue
      for (const rule of lintSource(text)) failures.push(`${next}: ${rule}`)
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

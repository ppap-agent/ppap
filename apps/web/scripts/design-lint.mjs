import {readdirSync, readFileSync, statSync} from 'node:fs'
import {dirname, join} from 'node:path'
import {fileURLToPath} from 'node:url'
import {moreContrast, tokens} from '../src/ui/tokens.ts'

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

const rawLayout = ['div', 'section', 'main', 'header', 'footer', 'nav', 'article', 'aside', 'form', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'ul', 'ol', 'li', 'fieldset', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'span', 'dl', 'dt', 'dd', 'pre', 'iframe', 'code', 'strong', 'em', 'small']
const rawControls = ['button', 'input', 'select', 'textarea', 'a', 'label']

const layoutPattern = new RegExp(`</?(?:${rawLayout.join('|')})\\b`)
const controlPattern = new RegExp(`</?(?:${rawControls.join('|')})\\b`)

const designRoots = ['color', 'background', 'border', 'outline', 'padding', 'margin', 'gap', 'font', 'letter-spacing', 'line-height', 'box-shadow', 'text-shadow', 'min-height', 'min-width', 'max-height', 'max-width', 'height', 'width', 'column-gap', 'row-gap', 'top', 'right', 'bottom', 'left', 'inset']
const notDesign = new Set(['border-collapse', 'border-spacing', 'font-synthesis', 'color-scheme'])
const keywords = new Set(['transparent', 'inherit', 'initial', 'unset', 'revert', 'none', 'currentcolor', 'solid', 'dashed', 'dotted', 'double', 'auto', 'normal'])
const viewportUnits = new Set(['dvh', 'dvw', 'vh', 'vw', 'vmin', 'vmax', 'svh', 'svw', 'lvh', 'lvw'])
const functions = new Set(['calc', 'min', 'max', 'minmax', 'clamp'])
const styleKeys = ['background', 'color', 'padding', 'paddingInline', 'paddingBlock', 'margin', 'gap', 'border', 'borderRadius', 'fontSize', 'fontWeight', 'fontFamily', 'lineHeight', 'letterSpacing', 'boxShadow', 'minHeight', 'minWidth', 'maxHeight', 'maxWidth', 'height', 'width', 'outline', 'outlineOffset']

function collectNames(value, prefix = '', out = new Set()) {
  if (typeof value === 'string') {
    out.add(prefix)
    return out
  }
  if (!value || typeof value !== 'object') return out
  for (const [key, child] of Object.entries(value)) collectNames(child, prefix ? `${prefix}-${key}` : key, out)
  return out
}

export const tokenNames = collectNames(tokens)
collectNames(moreContrast, '', tokenNames)

export function lintSource(source) {
  const violations = []
  if (/\bclassName\s*=/.test(source) || /\bclassName\s*:/.test(source)) violations.push('no-classname')
  if (/\bstyle\s*=\s*\{/.test(source) || /\bstyle\s*:/.test(source)) violations.push('no-inline-style')
  if (layoutPattern.test(source)) violations.push('no-raw-layout')
  if (controlPattern.test(source)) violations.push('no-raw-controls')
  if (/from\s+['"](?:stylex|@stylexjs\/stylex|styled-components|@emotion\/)/.test(source) || /import\s+['"][^'"]+\.css['"]/.test(source) || /var\(--/.test(source) || /--pc-/.test(source) || /from\s+['"][^'"]*ui\/system\//.test(source) || /from\s+['"][^'"]*\/system\/(?:layout|controls|dom|tokenCss|icons|chrome|workspace|dialog|data)\b/.test(source)) {
    violations.push('no-direct-styling-backend')
  }
  if (/['"]#[0-9a-fA-F]{3,8}['"]/.test(source) || /\brgba?\(/.test(source) || /\bhsla?\(/.test(source) || /\d+px\b/.test(source)) {
    violations.push('no-design-literals')
  }
  if (/\bsize\s*=\s*\{\s*\d+/.test(source)) violations.push('no-raw-icon-size')
  return violations
}

function isDesignProp(prop) {
  if (notDesign.has(prop)) return false
  return designRoots.some(root => prop === root || prop.startsWith(`${root}-`))
}

function readNumber(source, index) {
  let cursor = index
  if (source[cursor] === '+' || source[cursor] === '-') cursor += 1
  const start = index
  let digits = false
  let dot = false
  if (source[cursor] === '.') {
    dot = true
    cursor += 1
  }
  while (cursor < source.length && /[0-9]/.test(source[cursor])) {
    digits = true
    cursor += 1
  }
  if (!dot && source[cursor] === '.') {
    cursor += 1
    while (cursor < source.length && /[0-9]/.test(source[cursor])) {
      digits = true
      cursor += 1
    }
  }
  if (!digits) return null
  return {text: source.slice(start, cursor), end: cursor}
}

function readIdent(source, index) {
  let cursor = index
  while (cursor < source.length && /[A-Za-z_\u0080-\uFFFF0-9-]/.test(source[cursor])) cursor += 1
  return {word: source.slice(index, cursor), end: cursor}
}

function matchingParen(source, open) {
  let depth = 1
  for (let cursor = open + 1; cursor < source.length; cursor += 1) {
    if (source[cursor] === '(') depth += 1
    else if (source[cursor] === ')') {
      depth -= 1
      if (depth === 0) return cursor
    }
  }
  return source.length
}

function knownToken(name, names) {
  return name === 'preview' || names.has(name)
}

function walkValue(source, start, end, names, inFunction) {
  let cursor = start
  while (cursor < end) {
    while (cursor < end && /\s/.test(source[cursor])) cursor += 1
    if (cursor >= end) break
    const char = source[cursor]
    if (char === ',' || char === '/' || char === '+' || char === '*') {
      cursor += 1
      continue
    }
    if (char === '-' && source[cursor + 1] !== '-' && !/[0-9.]/.test(source[cursor + 1] || '')) {
      cursor += 1
      continue
    }
    if (/[0-9.+-]/.test(char)) {
      const number = readNumber(source, cursor)
      if (!number) return `unreadable number at ${source.slice(cursor, cursor + 12)}`
      let unit = ''
      let next = number.end
      if (source[next] === '%') {
        unit = '%'
        next += 1
      } else if (/[A-Za-z%]/.test(source[next] || '')) {
        const ident = readIdent(source, next)
        unit = ident.word
        next = ident.end
      }
      if (!unit) {
        if (number.text === '0' || number.text === '+0' || number.text === '-0' || inFunction) {
          cursor = next
          continue
        }
        return `unitless ${number.text}`
      }
      if (unit === '%' || unit === 'fr' || viewportUnits.has(unit)) {
        cursor = next
        continue
      }
      return `raw length ${number.text}${unit}`
    }
    if (/[A-Za-z_\u0080-\uFFFF-]/.test(char)) {
      const ident = readIdent(source, cursor)
      if (source[ident.end] === '(') {
        const name = ident.word.toLowerCase()
        const close = matchingParen(source, ident.end)
        if (name === 'var') {
          const inner = source.slice(ident.end + 1, close).trim()
          const comma = topComma(inner)
          const token = (comma < 0 ? inner : inner.slice(0, comma)).trim()
          if (!/^--[A-Za-z0-9-]+$/.test(token) || !knownToken(token.slice(2), names)) return `unknown token ${token || inner}`
          if (comma >= 0) {
            const fallback = walkValue(inner.slice(comma + 1), 0, inner.length - comma - 1, names, true)
            if (fallback) return fallback
          }
          cursor = close + 1
          continue
        }
        if (name === 'env') {
          cursor = close + 1
          continue
        }
        if (functions.has(name)) {
          const nested = walkValue(source, ident.end + 1, close, names, true)
          if (nested) return nested
          cursor = close + 1
          continue
        }
        return `function ${ident.word}`
      }
      if (!keywords.has(ident.word.toLowerCase())) return `keyword ${ident.word}`
      cursor = ident.end
      continue
    }
    return `character ${char}`
  }
  return null
}

function topComma(source) {
  let depth = 0
  for (let cursor = 0; cursor < source.length; cursor += 1) {
    if (source[cursor] === '(') depth += 1
    else if (source[cursor] === ')') depth -= 1
    else if (source[cursor] === ',' && depth === 0) return cursor
  }
  return -1
}

export function lintDesignValue(value, names = tokenNames) {
  return walkValue(value.trim(), 0, value.trim().length, names, false)
}

function declarationBlocks(css) {
  const blocks = []
  function walk(text) {
    let cursor = 0
    while (cursor < text.length) {
      const open = text.indexOf('{', cursor)
      if (open < 0) return
      let depth = 1
      let next = open + 1
      while (next < text.length && depth) {
        if (text[next] === '{') depth += 1
        else if (text[next] === '}') depth -= 1
        next += 1
      }
      const body = text.slice(open + 1, next - 1)
      if (body.includes('{')) walk(body)
      else blocks.push(body)
      cursor = next
    }
  }
  walk(css.replace(/\/\*[\s\S]*?\*\//g, ''))
  return blocks
}

function declarations(block) {
  const found = []
  let depth = 0
  let current = ''
  for (const char of block) {
    if (char === '(') depth += 1
    else if (char === ')' && depth) depth -= 1
    if (char === ';' && depth === 0) {
      found.push(current)
      current = ''
    } else current += char
  }
  if (current.trim()) found.push(current)
  return found
}

export function lintStylesheet(css, names = tokenNames) {
  const failures = []
  for (const block of declarationBlocks(css)) {
    for (const declaration of declarations(block)) {
      const split = declaration.indexOf(':')
      if (split < 0) continue
      const prop = declaration.slice(0, split).trim().toLowerCase()
      const value = declaration.slice(split + 1).replace(/\s*!important\s*$/i, '').trim()
      if (!prop || prop.startsWith('--') || !value) continue
      if (/#[0-9a-fA-F]{3,8}\b/.test(value) || /\brgba?\(/.test(value) || /\bhsla?\(/.test(value) || /(?:^|[^\w-])\d*\.?\d+(?:px|rem|em)\b/.test(value)) {
        failures.push(`${prop}: raw design value ${value}`)
      }
      if (!isDesignProp(prop)) continue
      const problem = lintDesignValue(value, names)
      if (problem) failures.push(`${prop}: ${problem}`)
    }
  }
  return failures
}

function lintSystemModule(source, names) {
  const failures = []
  const masked = source.replace(/var\(\s*--([A-Za-z0-9-]*)\$\{[^}]+\}/g, (full, prefix) => {
    const legal = prefix.endsWith('-') && [...names].some(name => name.startsWith(prefix))
    if (!legal) failures.push(`unknown token prefix --${prefix}`)
    return 'var(--color-text-primary'
  })
  for (const match of masked.matchAll(/var\(\s*--([A-Za-z0-9-]+)/g)) {
    if (!knownToken(match[1], names)) failures.push(`unknown token --${match[1]}`)
  }
  const pattern = new RegExp(`\\b(${styleKeys.join('|')})\\s*:\\s*(['"\`])([\\s\\S]*?)\\2`, 'g')
  for (const match of masked.matchAll(pattern)) {
    const problem = lintDesignValue(match[3], names)
    if (problem) failures.push(`${match[1]}: ${problem}`)
  }
  return failures
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
      if (name.endsWith('.contract.tsx')) continue
      const text = readFileSync(path, 'utf8')
      const backend = next.startsWith('src/ui/system/')
      const tokenFile = next === 'src/ui/tokens.ts'
      if (text.includes('--pc-') || text.includes('--ui-')) failures.push(`${next}: no-legacy-alias`)
      if (name.endsWith('.css') && !backend) failures.push(`${next}: no-feature-css`)
      if (backend && name.endsWith('.css')) {
        for (const problem of lintStylesheet(text)) failures.push(`${next}: ${problem}`)
      }
      if (backend && /\.tsx?$/.test(name) && !tokenFile && !name.endsWith('.test.ts')) {
        for (const problem of lintSystemModule(text, tokenNames)) failures.push(`${next}: ${problem}`)
      }
      if (backend || !next.startsWith('src/') || next.startsWith('src/ui/') || name.endsWith('.test.ts')) {
        if (next.startsWith('src/') && next.endsWith('.tsx') && !next.endsWith('/icons.tsx') && /\bsize\s*=\s*\{\s*\d+/.test(text)) {
          failures.push(`${next}: no-raw-icon-size`)
        }
        continue
      }
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

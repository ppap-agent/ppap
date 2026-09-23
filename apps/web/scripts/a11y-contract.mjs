import {readFileSync} from 'node:fs'
import {dirname, join} from 'node:path'
import {fileURLToPath} from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '../src/ui')
const css = readFileSync(join(root, 'system/system.css'), 'utf8')
const tokens = readFileSync(join(root, 'tokens.ts'), 'utf8')
const viewport = readFileSync(join(root, 'system/viewport.ts'), 'utf8')

const required = [
  ['desktop sidebar', /\[data-ui="sidebar"\]\s*\{[^}]*width:\s*var\(--size-sidebar\)/],
  ['tablet shell', /@media \(min-width: 801px\) and \(max-width: 1100px\)/],
  ['mobile shell', /@media \(max-width: 800px\)/],
  ['compact workspace', /@media \(max-width: 1023px\)/],
  ['touch target', /@media \(pointer: coarse\)[\s\S]*?min-height:\s*var\(--control-touch\)/],
  ['reduced motion', /@media \(prefers-reduced-motion: reduce\)[\s\S]*?animation:\s*none/],
  ['focus ring', /:focus-visible[\s\S]*?outline:\s*var\(--size-focus\) solid var\(--color-action-primary\)/],
]

const failures = []
for (const [name, pattern] of required) if (!pattern.test(css)) failures.push(`system.css missing ${name}`)
if (!tokens.includes("touch: '44px'")) failures.push('touch target token is not 44px')
if (!tokens.includes("focus: '2px'")) failures.push('focus ring token is not 2px')
if (!viewport.includes('1023px') || !css.includes('1023px')) failures.push('compact viewport is not shared')
const tokensCss = readFileSync(join(root, 'system/tokenCss.ts'), 'utf8')
if (!tokensCss.includes('prefers-contrast: more')) failures.push('increased contrast does not override tokens')
if (failures.length) {
  console.error(failures.join('\n'))
  process.exit(1)
}

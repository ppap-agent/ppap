import {moreContrast, tokens} from '../tokens'

function declarations(prefix: string, value: unknown, out: string[]) {
  if (typeof value === 'string') {
    out.push(`--${prefix}:${value}`)
    return
  }
  if (!value || typeof value !== 'object') return
  for (const [key, child] of Object.entries(value)) {
    declarations(prefix ? `${prefix}-${key}` : key, child, out)
  }
}

export function tokenStyleSheet() {
  const lines: string[] = []
  const contrast: string[] = []
  declarations('', tokens, lines)
  declarations('', moreContrast, contrast)
  return `:root{${lines.join(';')}}@media (prefers-contrast: more){:root{${contrast.join(';')}}}`
}

export function installTokens() {
  if (typeof document === 'undefined') return
  if (document.head.querySelector('style[data-ui-tokens]')) return
  const style = document.createElement('style')
  style.dataset.uiTokens = 'true'
  style.textContent = tokenStyleSheet()
  document.head.appendChild(style)
}

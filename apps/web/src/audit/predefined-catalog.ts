/** Navigation comes only from real source paths returned by GET /suites. */
import type {components} from '../generated/openapi'

export type PredefinedEntry = components['schemas']['SuitePredefined']
export type InstalledPredefinedEntry = PredefinedEntry & {suite_key: string; revision_id: string}
export type PredefinedNode = {path: string; name: string; entry?: PredefinedEntry; children: PredefinedNode[]}
export type RulesArea = 'predefined' | 'enterprise' | 'requirements'

export function directoryParts(value: string): string[] | null {
  const parts = value.split('/')
  if (!/^v[0-9]+$/.test(parts[0] || '') || parts.some(part => !part || part.trim() !== part || ['.', '..'].includes(part) || /[:\\\x00]/.test(part))) return null
  return parts
}

export function predefinedPath(value: string): {version: string; customer: string; leaf: string} | null {
  const parts = directoryParts(value)
  return parts?.length === 3 ? {version: parts[0]!, customer: parts[1]!, leaf: parts[2]!} : null
}

export function canOpenPredefined(entry: PredefinedEntry): entry is InstalledPredefinedEntry {
  const parts = predefinedPath(entry.path)
  return entry.availability === 'installed' && Boolean(entry.revision_id) && Boolean(parts && entry.suite_key === `${parts.customer}:${parts.leaf}`)
}

export function predefinedTree(entries: PredefinedEntry[]): {nodes: PredefinedNode[]; rejected: PredefinedEntry[]} {
  const roots: PredefinedNode[] = [], indexed = new Map<string, PredefinedNode>(), rejected: PredefinedEntry[] = []
  const counts = new Map<string, number>()
  for (const entry of entries) counts.set(entry.path, (counts.get(entry.path) || 0) + 1)
  for (const entry of entries) {
    const parts = directoryParts(entry.path)
    if (!parts || counts.get(entry.path) !== 1 || entry.availability !== 'historical' && parts.length !== 3) {rejected.push(entry); continue}
    let siblings = roots
    for (let index = 0; index < parts.length; index++) {
      const path = parts.slice(0, index + 1).join('/')
      let node = indexed.get(path)
      if (!node) {node = {path, name: parts[index]!, children: []}; indexed.set(path, node); siblings.push(node)}
      if (index === parts.length - 1) node.entry = entry
      siblings = node.children
    }
  }
  roots.sort((a, b) => b.name.localeCompare(a.name, 'en', {numeric: true}))
  return {nodes: roots, rejected}
}

export function checkerLabel(entry: PredefinedEntry): string {
  const group = predefinedPath(entry.path)?.leaf
  if (!group) return entry.title
  const acronym = entry.title.match(/[（(]([A-Z][A-Z0-9-]{2,})[）)]/)?.[1]
  const title = acronym || entry.title.replace(/[（(][A-Z][A-Z0-9-]*[）)]/g, '').replace(/(?:审核|检查)?规则$/, '').trim()
  return `${group} · ${title || entry.title}`
}

export function readPredefinedPath(url: URL): string {return url.searchParams.get('checkerPath') || ''}
export function predefinedScope(path: string): string {return `predefined:${path}`}

export function readRulesArea(url: URL): RulesArea {
  if (url.searchParams.has('requirement') || url.searchParams.get('rulesArea') === 'requirements') return 'requirements'
  if (url.searchParams.has('checkerSuite') && !url.searchParams.has('checkerPath')) return 'enterprise'
  return 'predefined'
}

export function rulesAreaAddress(url: URL, area: RulesArea): URL {
  const next = new URL(url)
  next.searchParams.delete('requirement'); next.searchParams.delete('reqVersion'); next.searchParams.delete('reqTab')
  if (area === 'requirements') next.searchParams.set('rulesArea', area)
  else next.searchParams.delete('rulesArea')
  if (area === 'enterprise') {next.searchParams.delete('checkerPath'); if (!next.searchParams.has('checkerSuite')) next.searchParams.set('checkerSuite', '')}
  else if (area === 'predefined') next.searchParams.delete('checkerSuite')
  return next
}

/** The source revision stays official even when the tenant adopted a different one. */
export function sourceIdentity(entry: InstalledPredefinedEntry, adoptedRevisionId: string | null | undefined): string {
  if (!adoptedRevisionId) return `来源 ${entry.path} 绑定官方修订 ${entry.revision_id}。企业尚未采用 ${entry.suite_key}。`
  if (adoptedRevisionId === entry.revision_id) return `来源 ${entry.path} 的官方修订 ${entry.revision_id} 与企业当前采用的修订相同。`
  return `来源 ${entry.path} 的官方修订 ${entry.revision_id} 与企业当前采用的修订 ${adoptedRevisionId} 不同。此目录仍只打开该来源的官方修订。`
}

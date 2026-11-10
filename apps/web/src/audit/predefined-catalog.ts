/** Navigation comes only from real source paths returned by GET /suites. */
import type {components} from '../generated/openapi'

export type PredefinedEntry = components['schemas']['SuitePredefined']
export type InstalledPredefinedEntry = PredefinedEntry & {suite_key: string; revision_id: string}
export type PredefinedNode = {path: string; name: string; entry?: PredefinedEntry; children: PredefinedNode[]}
export type RulesArea = 'templates' | 'automation' | 'predefined'

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

/** Rule templates are the default. Older checker addresses keep working: a chosen Suite opens automatic review, a source path the program directory. */
export function readRulesArea(url: URL): RulesArea {
  const area = url.searchParams.get('rulesArea')
  if (url.searchParams.has('requirement') || url.searchParams.has('template') || url.searchParams.has('systemRule') || area === 'requirements') return 'templates'
  if (url.searchParams.has('checkerPath') || area === 'predefined') return 'predefined'
  if (url.searchParams.has('checkerSuite') || area === 'automation' || area === 'enterprise') return 'automation'
  return 'templates'
}

/** The source revision stays official even when the tenant adopted a different one. */
export function sourceIdentity(entry: InstalledPredefinedEntry, adoptedRevisionId: string | null | undefined): string {
  if (!adoptedRevisionId) return '本企业尚未采用这项检查。'
  if (adoptedRevisionId === entry.revision_id) return '本企业正在使用这个官方版本。'
  return '本企业正在使用另一个版本；这里显示的是官方版本。'
}

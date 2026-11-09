/** Group installed directory leaves by the path the API returned. No version or customer registry. */

export type PredefinedEntry = {path: string; title: string; suite_key: string; revision_id: string; clause_count: number; bound_clause_count: number; evidence_reader_clause_ids: string[]}
export type PredefinedCustomer = {customer: string; entries: PredefinedEntry[]}
export type PredefinedGroup = {version: string; customers: PredefinedCustomer[]}

const DIRECTORY_PATH = /^v[0-9]+\/[^/]+\/[^/]+$/

export function predefinedPath(value: string): {version: string; customer: string; leaf: string} | null {
  if (!DIRECTORY_PATH.test(value)) return null
  const [version, customer, leaf] = value.split('/')
  if (!version || !customer || !leaf) return null
  return {version, customer, leaf}
}

export function canOpenPredefined(entry: Pick<PredefinedEntry, 'path' | 'revision_id'>): boolean {
  return Boolean(entry.revision_id) && predefinedPath(entry.path) !== null
}

/** Keep the API order. A later v6 path appears because it is in the list, not because the UI names it. */
export function groupPredefined(entries: PredefinedEntry[]): {groups: PredefinedGroup[]; rejected: PredefinedEntry[]} {
  const rejected: PredefinedEntry[] = []
  const versions = new Map<string, Map<string, PredefinedEntry[]>>()
  for (const entry of entries) {
    const parts = predefinedPath(entry.path)
    if (!parts || !entry.revision_id) {
      rejected.push(entry)
      continue
    }
    const customers = versions.get(parts.version) ?? new Map<string, PredefinedEntry[]>()
    customers.set(parts.customer, [...(customers.get(parts.customer) ?? []), entry])
    versions.set(parts.version, customers)
  }
  return {
    groups: [...versions.entries()].map(([version, customers]) => ({
      version,
      customers: [...customers.entries()].map(([customer, items]) => ({customer, entries: items})),
    })),
    rejected,
  }
}

export function readPredefinedPath(url: URL): string {
  return url.searchParams.get('checkerPath') || ''
}

/** Declared reader ids are not a claim that those clauses are business-correct. */
export function capabilityDisclosure(entry: Pick<PredefinedEntry, 'clause_count' | 'bound_clause_count' | 'evidence_reader_clause_ids'>): string {
  const bound = `${entry.bound_clause_count}/${entry.clause_count}`
  if (entry.evidence_reader_clause_ids.length === 0) return `已绑定 ${bound} 项。没有声明证据读取，不能判定为通过。`
  return `已声明证据读取 ${entry.evidence_reader_clause_ids.join('、')}。绑定 ${bound} 项。未声明证据读取的条款不能据此判定为通过。`
}

export function capabilitySummary(entries: readonly Pick<PredefinedEntry, 'evidence_reader_clause_ids'>[]): string {
  const readers = entries.reduce((sum, entry) => sum + entry.evidence_reader_clause_ids.length, 0)
  return `当前列出 ${entries.length} 个预定义来源，已声明证据读取 ${readers} 项。来源数量不是业务规则准确率。`
}

export function predefinedScope(path: string): string {
  return `predefined:${path}`
}

/** The source revision stays the official id even when the tenant has adopted a different one. */
export function sourceIdentity(entry: Pick<PredefinedEntry, 'path' | 'suite_key' | 'revision_id'>, adoptedRevisionId: string | null | undefined): string {
  if (!adoptedRevisionId) return `来源 ${entry.path} 绑定官方修订 ${entry.revision_id}。企业尚未采用 ${entry.suite_key}。`
  if (adoptedRevisionId === entry.revision_id) return `来源 ${entry.path} 的官方修订 ${entry.revision_id} 与企业当前采用的修订相同。`
  return `来源 ${entry.path} 的官方修订 ${entry.revision_id} 与企业当前采用的修订 ${adoptedRevisionId} 不同。此目录仍只打开该来源的官方修订。`
}

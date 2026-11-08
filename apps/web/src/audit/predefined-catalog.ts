/** Group installed directory leaves by the path the API returned. No version or customer registry. */

export type PredefinedEntry = {path: string; title: string; suite_key: string; revision_id: string}
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

export function predefinedScope(path: string): string {
  return `predefined:${path}`
}

/** The source revision stays the official id even when the tenant has adopted a different one. */
export function sourceIdentity(entry: Pick<PredefinedEntry, 'path' | 'suite_key' | 'revision_id'>, adoptedRevisionId: string | null | undefined): string {
  if (!adoptedRevisionId) return `来源 ${entry.path} 绑定官方修订 ${entry.revision_id}。企业尚未采用 ${entry.suite_key}。`
  if (adoptedRevisionId === entry.revision_id) return `来源 ${entry.path} 的官方修订 ${entry.revision_id} 与企业当前采用的修订相同。`
  return `来源 ${entry.path} 的官方修订 ${entry.revision_id} 与企业当前采用的修订 ${adoptedRevisionId} 不同。此目录仍只打开该来源的官方修订。`
}

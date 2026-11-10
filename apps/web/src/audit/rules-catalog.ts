export type SourceKind = 'publishedRule' | 'officialSuite' | 'enterpriseSuite'

export type CatalogRow = {
  key: string
  title: string
  source: '已发布规则' | '系统提供' | '企业上传'
  sourceKind: SourceKind
  detail: string
  action: string
  href: string
  ruleVersionId: string | null
  suiteKey: string | null
  documentType: string
}

export function catalogQuery(value: string) {
  return value.trim().toLocaleLowerCase()
}

export function matchesCatalog(row: CatalogRow, query: string) {
  const needle = catalogQuery(query)
  if (!needle) return true
  return `${row.title} ${row.source} ${row.detail} ${row.documentType} ${row.action}`.toLocaleLowerCase().includes(needle)
}

export function officialCatalogTitle(path: string, title: string): {title: string; aliases: string} {
  const parts = path.split('/')
  const customer = parts.length === 3 && /^v[0-9]+$/.test(parts[0] || '') ? parts[1] : ''
  const acronym = title.match(/[（(]([A-Z][A-Z0-9-]{2,})[）)]/)?.[1] ?? ''
  const short = title.replace(/[（(][A-Z][A-Z0-9-]*[）)]/g, '').replace(/(?:审核|检查)?规则$/, '').replace(/^\d+\s+/, '').trim()
  return {
    title: customer && short ? `${short} · ${customer}` : title,
    aliases: [title, acronym, customer ? parts[2] || '' : ''].filter(Boolean).join(' '),
  }
}

export function buildRuleCatalog(input: {
  rules: {id: string; title: string; documentType: string; customer: string; part: string; suiteKey: string | null}[]
  official: {path: string; title: string}[]
  enterprise: {suiteKey: string; title: string; ready: boolean}[]
}): CatalogRow[] {
  const linked = new Set(input.rules.flatMap(rule => rule.suiteKey ? [rule.suiteKey] : []))
  const published = input.rules.map(rule => ({
    key: `published:${rule.id}`,
    title: rule.title,
    source: '已发布规则' as const,
    sourceKind: 'publishedRule' as const,
    detail: [rule.documentType, rule.customer, rule.part].filter(Boolean).join(' · '),
    action: '核对规则',
    href: rule.id,
    ruleVersionId: rule.id,
    suiteKey: rule.suiteKey,
    documentType: rule.documentType,
  }))
  const official = input.official.map(item => {
    const named = officialCatalogTitle(item.path, item.title)
    return {
      key: `official:${item.path}`,
      title: named.title,
      source: '系统提供' as const,
      sourceKind: 'officialSuite' as const,
      detail: '',
      action: '查看规则',
      href: item.path,
      ruleVersionId: null,
      suiteKey: null,
      documentType: [named.aliases, item.path].filter(Boolean).join(' '),
    }
  })
  const enterprise = input.enterprise.filter(item => !linked.has(item.suiteKey)).map(item => ({
    key: `enterprise:${item.suiteKey}`,
    title: item.title,
    source: '企业上传' as const,
    sourceKind: 'enterpriseSuite' as const,
    detail: '',
    action: item.ready ? '查看规则' : '检查功能尚待完善',
    href: item.suiteKey,
    ruleVersionId: null,
    suiteKey: item.suiteKey,
    documentType: item.suiteKey,
  }))
  return [...published, ...official, ...enterprise]
}

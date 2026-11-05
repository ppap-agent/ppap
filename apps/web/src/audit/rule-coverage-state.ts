import type {components} from '../generated/openapi'

type Entry = components['schemas']['RuleLibraryEntry']
export type CustomerCoverage = {customer: string; covered: number; total: number; types: {type: string; rules: string[]}[]}

/** Coverage counts each customer's own rules plus all-customer rules, using only the newest version of each rule. */
export function ruleCoverage(entries: Entry[], types: string[]): CustomerCoverage[] {
  const replaced = new Set(entries.map(row => row.predecessor_id))
  const current = entries.filter(row => !replaced.has(row.id) && row.document_type)
  const names = new Map<string, string>()
  for (const row of current) if (!row.scope.allCustomers && row.scope.customer.trim()) names.set(row.scope.customer.trim().toLowerCase(), names.get(row.scope.customer.trim().toLowerCase()) ?? row.scope.customer.trim())
  const shared = current.filter(row => row.scope.allCustomers)
  const rows = [...names].map(([key, customer]) => ({customer, applied: [...shared, ...current.filter(row => row.scope.customer.trim().toLowerCase() === key && !row.scope.allCustomers)]}))
  if (!rows.length && shared.length) rows.push({customer: '全部客户', applied: shared})
  return rows.map(({customer, applied}) => {
    const covered = types.map(type => ({type, rules: applied.filter(row => row.document_type === type).map(row => row.title)}))
    return {customer, covered: covered.filter(row => row.rules.length).length, total: types.length, types: covered}
  }).sort((a, b) => a.customer.localeCompare(b.customer, 'zh-CN'))
}

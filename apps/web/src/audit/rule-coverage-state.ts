import type {components} from '../generated/openapi'

type Rule = components['schemas']['Rule']
export type CoverageType = {type: string; label: string; rules: string[]}
export type CustomerCoverage = {customer: string; covered: number; total: number; types: CoverageType[]}

const key = (value: string) => value.trim().toLowerCase()

/**
 * Standard types are compared across customers; a type outside `standardTypes` (such as UAES E07)
 * belongs to its own customers and never counts against the others.
 */
export function ruleCoverage(rules: Rule[], standardTypes: string[]): CustomerCoverage[] {
  const replaced = new Set(rules.map(rule => rule.predecessor_id))
  const current = rules.filter(rule => rule.kind === 'engine' && rule.type && !replaced.has(rule.id))
  const names = new Map<string, string>()
  for (const rule of current) for (const name of rule.customers ?? []) if (name.trim() && !names.has(key(name))) names.set(key(name), name.trim())
  const shared = current.filter(rule => rule.customer_scope === 'all' && !rule.customers?.length)
  const comparable = [...new Set(current.map(rule => rule.type!).filter(type => standardTypes.includes(type)))]
  const rows = [...names].map(([id, customer]) => ({customer, applied: [...shared, ...current.filter(rule => rule.customers?.some(name => key(name) === id))]}))
  if (!rows.length && shared.length) rows.push({customer: '全部客户', applied: shared})
  return rows.map(({customer, applied}) => {
    const own = [...new Set(applied.map(rule => rule.type!).filter(type => !standardTypes.includes(type)))]
    const types = [...comparable.sort((a, b) => standardTypes.indexOf(a) - standardTypes.indexOf(b)), ...own.sort()].map(type => {
      const matching = applied.filter(rule => rule.type === type)
      return {type, label: standardTypes.includes(type) ? type : matching[0].title.replace(/审核规则$/, ''), rules: [...new Set(matching.map(rule => rule.title))]}
    })
    return {customer, covered: types.filter(row => row.rules.length).length, total: types.length, types}
  }).sort((a, b) => a.customer.localeCompare(b.customer, 'zh-CN'))
}

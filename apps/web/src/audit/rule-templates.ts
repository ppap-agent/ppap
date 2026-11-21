import type {components} from '../generated/openapi'
import {documentTypeLabel} from './status-labels.ts'
import {registeredCustomer, type RegisteredCustomer} from './customer-register.ts'

type Rule = components['schemas']['Rule']

const SHARED_TEMPLATE = 'shared'
const PENDING_TEMPLATE = 'pending'

/** The rule category a published rule belongs to in the confirm step's rule picker, as listed by GET /rules. */
export function ruleTemplateKey(rule: Pick<Rule, 'rule_profile' | 'customers' | 'customer_scope'>): string {
  const customer = rule.customers?.[0]?.trim()
  if (rule.rule_profile === 'v4' && customer) return `system:${customer}`
  if (rule.customer_scope === 'all') return SHARED_TEMPLATE
  return customer ? `customer:${customer}` : PENDING_TEMPLATE
}

export function templateName(key: string): string {
  if (key === SHARED_TEMPLATE) return '通用规则'
  if (key === PENDING_TEMPLATE) return '待整理'
  if (key.startsWith('system:')) return `${key.slice('system:'.length)} 规则`
  return key.replace(/^customer:/, '')
}

export type Rulebook = components['schemas']['RulebookRevision']
/** How people name a rulebook revision: 规范性条款 v1.0 修订 0. */
export const rulebookLabel = (rulebook: Rulebook) => `${rulebook.name} ${rulebook.version} 修订 ${rulebook.revision}`
/** `checked` counts the clauses the checker is aligned with; the others are not checked until it catches up. */
export type RuleRow = {id: string; title: string; clauses: number | null; updatedAt: string; rulebook: string | null; checked: number | null}
export type TypeRules = {code: string; type: string; label: string; rules: RuleRow[]}
/** One registered customer on the rules page: its type set M with the current rules of each type; `types` is null when M is not configured. */
export type CustomerRules = {customer: RegisteredCustomer; types: TypeRules[] | null}

/**
 * The rules page: one entry per registered customer, its file types as M lists them, and the current rules of each.
 * A rule belongs to a customer only when it names one of the registered names; a rule outside M is not listed.
 */
export function customerRules(customers: RegisteredCustomer[], rules: Rule[]): CustomerRules[] {
  const replaced = new Set(rules.map(rule => rule.predecessor_id))
  const current = rules.filter(rule => rule.kind === 'engine' && !replaced.has(rule.id))
  return customers.map(customer => {
    const types = customer.coverage?.types
    if (!types?.length) return {customer, types: null}
    const own = current.filter(rule => (rule.customers ?? []).some(value => registeredCustomer([customer], value)))
    return {customer, types: types.map(type => ({
      code: type.code, type: type.rule_type, label: documentTypeLabel(type.rule_type),
      rules: own.filter(rule => rule.type === type.rule_type).map(rule => ({id: rule.id, title: rule.title, clauses: rule.clause_count ?? null, updatedAt: rule.created_at, rulebook: rule.rulebook ? rulebookLabel(rule.rulebook) : null, checked: rule.checked_clause_count ?? null}))
        .sort((a, b) => a.title.localeCompare(b.title, 'zh-CN')),
    }))}
  })
}

/** Rule coverage N_r/M exactly as the backend counted it; null without M. */
export function ruleCoverage(customer: RegisteredCustomer) {
  const coverage = customer.coverage
  return coverage?.types.length ? {covered: coverage.rule_types.length, total: coverage.types.length} : null
}

/** The one rulebook revision a customer's current rules come from, and how many of its clauses the checkers are aligned with; null otherwise. */
export function customerRulebook(entry: CustomerRules): {label: string; clauses: number; checked: number} | null {
  const rows = (entry.types ?? []).flatMap(type => type.rules).filter(rule => rule.rulebook)
  const labels = [...new Set(rows.map(rule => rule.rulebook))]
  if (labels.length !== 1) return null
  return {label: labels[0]!, clauses: rows.reduce((sum, rule) => sum + (rule.clauses ?? 0), 0), checked: rows.reduce((sum, rule) => sum + (rule.checked ?? 0), 0)}
}

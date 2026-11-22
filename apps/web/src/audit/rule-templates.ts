import type {components} from '../generated/openapi'
import {customerName, documentTypeName, type RegisteredCustomer, type TypeNames} from './customer-register.ts'

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

/** A rule category's name; a customer's built-in rules are named by the customer's registered name. */
export function templateName(key: string, customers: readonly RegisteredCustomer[] | null | undefined): string {
  if (key === SHARED_TEMPLATE) return '通用规则'
  if (key === PENDING_TEMPLATE) return '待整理'
  if (key.startsWith('system:')) return `${customerName(customers, key.slice('system:'.length))} 规则`
  return key.replace(/^customer:/, '')
}

/** The rules in force: engine rules no other version names as its predecessor. */
export function currentRules<Row extends Pick<Rule, 'id' | 'kind' | 'predecessor_id'>>(rules: Row[]): Row[] {
  const replaced = new Set(rules.map(rule => rule.predecessor_id))
  return rules.filter(rule => rule.kind === 'engine' && !replaced.has(rule.id))
}

export type RuleRow = {id: string; title: string; clauses: number | null; updatedAt: string}
export type TypeRules = {code: string; type: string; label: string; rules: RuleRow[]}
/** One registered customer on the rules page: its type set M with the current rules of each type; `types` is null when M is not configured. */
export type CustomerRules = {customer: RegisteredCustomer; types: TypeRules[] | null}

/**
 * The rules page: one entry per registered customer, its file types as M lists them, and the current rules of each.
 * A rule belongs to a customer only when it names the customer's key; a rule outside M is not listed.
 */
export function customerRules(customers: RegisteredCustomer[], typeNames: TypeNames, rules: Rule[]): CustomerRules[] {
  const current = currentRules(rules)
  return customers.map(customer => {
    const types = customer.coverage?.types
    if (!types?.length) return {customer, types: null}
    const own = current.filter(rule => (rule.customers ?? []).includes(customer.key))
    return {customer, types: types.map(type => ({
      code: type.code, type: type.rule_type, label: documentTypeName(typeNames, type.code),
      rules: own.filter(rule => rule.type === type.rule_type).map(rule => ({id: rule.id, title: rule.title, clauses: rule.clause_count ?? null, updatedAt: rule.created_at}))
        .sort((a, b) => a.title.localeCompare(b.title, 'zh-CN')),
    }))}
  })
}

export type Coverage = {covered: number; total: number}
/** Rule coverage N_r/M exactly as the backend counted it: types of M with a current rule, each once. Null without a customer or M. */
export function ruleCoverage(customer: RegisteredCustomer | undefined): Coverage | null {
  const coverage = customer?.coverage
  return coverage?.types.length ? {covered: coverage.rule_types.length, total: coverage.types.length} : null
}

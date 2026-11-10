import type {components} from '../generated/openapi'
import type {RequirementDirectoryCollection} from '../journey/requirement-directory'
import type {RequirementScope} from '../journey/requirement-library-state'

type Rule = components['schemas']['Rule']
type Catalog = components['schemas']['SuiteCatalog']

/** What an audit of this file type can do on its own; worded for the person choosing a template. */
export type Automation = {state: 'auto' | 'partial' | 'manual' | 'stale' | 'unverified' | 'unknown' | 'unreadable'; note?: string; suiteKey?: string}
export type TemplateRow = {
  type: string
  label: string
  ruleTitle: string
  version: string
  clauses: number | null
  target: {kind: 'requirement' | 'system'; id: string}
  automation: Automation
  draft: boolean
}
export type RuleTemplate = {key: string; name: string; source: 'system' | 'enterprise'; rows: TemplateRow[]; missing: string[]}

export const SHARED_TEMPLATE = 'shared'
export const PENDING_TEMPLATE = 'pending'
export const automationLabel: Record<Automation['state'], string> = {
  auto: '可自动审核', partial: '部分条款需人工', manual: '还不能自动审核', stale: '需重新验证', unverified: '待验证', unknown: '正在读取…', unreadable: '暂时读不到',
}

/** An uploaded rule belongs to the template its customer field names; unscoped rules are shared, unconfirmed ones wait to be sorted. */
export function enterpriseTemplateKey(scope: RequirementScope): string {
  if (scope.allCustomers) return SHARED_TEMPLATE
  const customer = scope.customer.trim()
  return customer ? `customer:${customer}` : PENDING_TEMPLATE
}

/** The same key from a published rule, as listed by GET /rules. */
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

const typeLabel = (type: string, title: string, standard: string[]) => standard.includes(type) || !type ? type || title : title.replace(/(?:审核)?规则$/, '').replace(/^[\d-]+\s*规则-?/, '').trim() || type

function automation(catalog: Catalog | 'failed' | null, suiteKey: string | null | undefined, preset: boolean): Automation {
  if (!suiteKey) return {state: 'manual', note: '审核时由人工逐条判断'}
  if (catalog === 'failed') return {state: 'unreadable', suiteKey}
  if (!catalog) return {state: 'unknown', suiteKey}
  const summary = catalog.suites.find(item => item.suite_key === suiteKey)
  if (summary?.rule_update_pending) return {state: 'stale', suiteKey}
  const entry = preset ? catalog.predefined?.find(item => item.suite_key === suiteKey && item.availability === 'installed') : undefined
  if (entry) {
    const readers = entry.evidence_reader_clause_ids?.length ?? 0, total = entry.clause_count ?? 0
    if (!readers) return {state: 'manual', note: '审核时由人工逐条判断', suiteKey}
    if (total && readers < total) return {state: 'partial', note: `${total} 条中 ${readers} 条可自动审核`, suiteKey}
    return {state: 'auto', suiteKey}
  }
  if (!summary) return {state: 'manual', note: '审核时由人工逐条判断', suiteKey}
  if (summary.current_revision_id && summary.verification_status === 'verified') return {state: 'auto', suiteKey}
  return {state: 'unverified', suiteKey}
}

const sortRows = (rows: TemplateRow[], standard: string[]) => rows.sort((a, b) => {
  const left = standard.indexOf(a.type), right = standard.indexOf(b.type)
  return (left < 0 ? standard.length : left) - (right < 0 ? standard.length : right) || a.label.localeCompare(b.label, 'zh-CN')
})

/**
 * Product presets form the system templates, one per customer. Uploaded rules form the enterprise
 * templates by their customer field. Nothing here is invented: a type without a rule is listed as missing.
 */
/** `catalog` is null while the checker catalog is read and 'failed' when it could not be. */
export function buildTemplates(input: {rules: Rule[]; library: RequirementDirectoryCollection[]; catalog: Catalog | 'failed' | null; standardTypes: string[]}): RuleTemplate[] {
  const {rules, library, catalog, standardTypes} = input
  const replaced = new Set(rules.map(rule => rule.predecessor_id))
  const templates = new Map<string, RuleTemplate>()
  const add = (key: string, source: RuleTemplate['source'], row: TemplateRow) => {
    const template = templates.get(key) ?? {key, name: templateName(key), source, rows: [], missing: []}
    template.rows.push(row)
    templates.set(key, template)
  }
  for (const rule of rules) {
    if (rule.kind !== 'engine' || rule.rule_profile !== 'v4' || replaced.has(rule.id)) continue
    const customer = rule.customers?.[0]?.trim()
    if (!customer) continue
    const type = rule.type ?? ''
    add(`system:${customer}`, 'system', {
      type, label: typeLabel(type, rule.title, standardTypes), ruleTitle: rule.title, version: '', clauses: rule.clause_count ?? null,
      target: {kind: 'system', id: rule.id}, automation: automation(catalog, rule.suite_key ?? (rule.preset_key ? `${customer}:${rule.preset_key}` : null), true), draft: false,
    })
  }
  for (const collection of library) {
    const latest = collection.draft ?? collection.versions.at(-1)
    if (!latest) continue
    add(enterpriseTemplateKey(latest.scope), 'enterprise', {
      type: latest.documentType, label: typeLabel(latest.documentType, latest.title || '待命名规则', standardTypes), ruleTitle: latest.title || '待命名规则',
      version: latest.version, clauses: latest.clauseCount, target: {kind: 'requirement', id: collection.id},
      automation: collection.draft ? {state: 'manual', note: '草稿保存后才会用于审核'} : automation(catalog, latest.suiteKey, false), draft: Boolean(collection.draft),
    })
  }
  const order = (template: RuleTemplate) => template.source === 'system' ? 0 : template.key === SHARED_TEMPLATE ? 1 : template.key === PENDING_TEMPLATE ? 3 : 2
  return [...templates.values()].map(template => {
    const present = new Set(template.rows.map(row => row.type))
    return {...template, rows: sortRows(template.rows, standardTypes), missing: template.source === 'system' ? standardTypes.filter(type => !present.has(type)) : []}
  }).sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name, 'zh-CN'))
}

export const automatedCount = (template: RuleTemplate) => template.rows.filter(row => ['auto', 'partial', 'stale'].includes(row.automation.state)).length
export const pendingRows = (template: RuleTemplate) => template.rows.filter(row => row.automation.state === 'stale')

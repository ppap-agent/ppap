import type {components} from '../generated/openapi'
import type {RequirementDirectoryCollection} from '../journey/requirement-directory'
import type {RequirementScope} from '../journey/requirement-library-state'

type Rule = components['schemas']['Rule']
type Catalog = components['schemas']['SuiteCatalog']

/** What an audit of this file type can do on its own; worded for the person choosing a template. */
export type Automation = {state: 'auto' | 'partial' | 'manual' | 'stale' | 'unverified' | 'inactive' | 'unknown' | 'unreadable'; note?: string; suiteKey?: string}
export type TemplateRow = {
  type: string
  label: string
  ruleTitle: string
  version: string
  clauses: number | null
  target: {kind: 'requirement' | 'system'; id: string}
  automation: Automation
  draft: boolean
  /** When this rule version was published or the draft last saved; empty when unknown. */
  updatedAt: string
}
export type RuleTemplate = {key: string; name: string; source: 'system' | 'enterprise'; rows: TemplateRow[]; missing: string[]}

export const SHARED_TEMPLATE = 'shared'
export const PENDING_TEMPLATE = 'pending'
export const automationLabel: Record<Automation['state'], string> = {
  auto: '可用于审核', partial: '部分条款由人判断', manual: '尚无检查程序', stale: '规则已更新', unverified: '未经验证', inactive: '未启用', unknown: '正在读取…', unreadable: '暂时读不到',
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

// The product's preset codes for the standard types (audit/presets.py); UAES source types are stored as their E code.
const standardCodes: Record<string, string> = {
  全功能检验报告: 'T01', 零部件设计验证计划和报告: 'T02', DV报告_PV报告_试验报告: 'T03', 材料产品及分供清单: 'T04', 检具验收报告: 'T05', 模具验收报告: 'T06',
  测量系统分析评价: 'T07', 过程能力研究: 'T08', 过程流程图: 'T09', 潜在过程失效模式及后果分析: 'T10', 控制计划: 'T11', 过程特殊特性清单: 'T12',
  设备清单: 'T13', 生产系统产能评估报告: 'T14', 项目问题清单: 'T15', 零部件年度试验要求: 'T16', 零件提交保证书: 'T17',
}
export const typeCode = (type: string) => standardCodes[type] ?? (/^E\d{2}$/.test(type) ? type : '')

const typeLabel = (type: string, title: string, standard: string[]) => standard.includes(type) || !type ? type || title : title.replace(/(?:审核)?规则$/, '').replace(/^[\d-]+\s*规则-?/, '').trim() || type

function automation(catalog: Catalog | 'failed' | null, suiteKey: string | null | undefined, preset: boolean): Automation {
  if (!suiteKey) return {state: 'manual', note: '审核时由人判断'}
  if (catalog === 'failed') return {state: 'unreadable', suiteKey}
  if (!catalog) return {state: 'unknown', suiteKey}
  const summary = catalog.suites.find(item => item.suite_key === suiteKey)
  if (summary?.rule_update_pending) return {state: 'stale', suiteKey}
  const entry = preset ? catalog.predefined?.find(item => item.suite_key === suiteKey && item.availability === 'installed') : undefined
  if (entry) {
    const readers = entry.evidence_reader_clause_ids?.length ?? 0, total = entry.clause_count ?? 0
    if (!readers) return {state: 'manual', note: '审核时由人判断', suiteKey}
    if (total && readers < total) return {state: 'partial', note: `${total} 条中 ${readers} 条有检查`, suiteKey}
    return {state: 'auto', suiteKey}
  }
  if (!summary) return {state: 'manual', note: '审核时由人判断', suiteKey}
  // An uploaded rule is offered for audit only once its program is generated on the rule page.
  if (summary.program_missing) return {state: 'manual', note: '在规则页生成检查程序后才能用于审核', suiteKey}
  // A program offered but never adopted does not run; the person adopts it on the rule page.
  if (!summary.current_revision_id) return {state: 'inactive', note: '在规则页“检查程序”中采用后才会使用', suiteKey}
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
      target: {kind: 'system', id: rule.id}, automation: automation(catalog, rule.suite_key ?? (rule.preset_key ? `${customer}:${rule.preset_key}` : null), true), draft: false, updatedAt: rule.created_at,
    })
  }
  for (const collection of library) {
    const latest = collection.draft ?? collection.versions.at(-1)
    if (!latest) continue
    add(enterpriseTemplateKey(latest.scope), 'enterprise', {
      type: latest.documentType, label: typeLabel(latest.documentType, latest.title || '待命名规则', standardTypes), ruleTitle: latest.title || '待命名规则',
      version: latest.version, clauses: latest.clauseCount, target: {kind: 'requirement', id: collection.id},
      automation: collection.draft ? {state: 'manual', note: '草稿保存后才会用于审核'} : automation(catalog, latest.suiteKey, false), draft: Boolean(collection.draft), updatedAt: latest.at,
    })
  }
  const order = (template: RuleTemplate) => template.source === 'system' ? 0 : template.key === SHARED_TEMPLATE ? 1 : template.key === PENDING_TEMPLATE ? 3 : 2
  return [...templates.values()].map(template => {
    const present = new Set(template.rows.map(row => row.type))
    return {...template, rows: sortRows(template.rows, standardTypes), missing: template.source === 'system' ? standardTypes.filter(type => !present.has(type)) : []}
  }).sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name, 'zh-CN'))
}

/** A ready program runs in new audits; one with some clauses left to people still runs. Anything else does not count toward coverage. */
export const effective = (automation: Automation) => automation.state === 'auto' || automation.state === 'partial'

/** Rows of one file type, in the template's order. */
export function typeGroups(template: RuleTemplate): {type: string; rows: TemplateRow[]}[] {
  const groups = new Map<string, TemplateRow[]>()
  for (const row of template.rows) groups.set(row.type || row.label, [...groups.get(row.type || row.label) ?? [], row])
  return [...groups].map(([type, rows]) => ({type, rows}))
}

/**
 * File types with an effective program out of the file types this template has rules for.
 * `known` is false until the checker catalog is read, so no number is shown in the meantime.
 */
export function coverage(template: RuleTemplate) {
  const groups = typeGroups(template)
  return {
    effective: groups.filter(group => group.rows.some(row => effective(row.automation))).length,
    total: groups.length,
    known: template.rows.every(row => row.automation.state !== 'unknown' && row.automation.state !== 'unreadable'),
  }
}
export const pendingRows = (template: RuleTemplate) => template.rows.filter(row => row.automation.state === 'stale')

import type {components} from '../generated/openapi'
import {documentTypeLabel, sourceDocumentNames} from './status-labels.ts'
import {automation, buildTemplates, coverage, typeGroups, typeLabel, type RuleTemplate} from './rule-templates.ts'

type Rule = components['schemas']['Rule']
type Catalog = components['schemas']['SuiteCatalog']
/** The same backend reads as the rules page: rules, standard types and the checker catalog. */
export type CoverageSource = {rules: Rule[]; types: string[]; catalog: Catalog | null}
export type Coverage = {supported: number; total: number}

const normalized = (value: string) => value.trim().toLowerCase().replace(/[（(].*?[)）]/g, '').replace(/汽车$/, '')
/** A customer is named by the person's picker ("理想") and by a rule ("理想汽车"); the shorter name must lead the longer one. */
export function sameCustomer(left: string, right: string): boolean {
  const a = normalized(left), b = normalized(right)
  return Boolean(a && b) && (a === b || a.startsWith(b) || b.startsWith(a))
}

/**
 * The type a product checker leaf audits, read from its title: the standard type or source document it names.
 * A title naming none (for example "1-2规则-测量报告") stays its own type; nothing is guessed beyond the words.
 */
function leafType(title: string, standardTypes: string[]): string {
  const named = [...standardTypes.map(type => [type, type]), ...Object.entries(sourceDocumentNames)].filter(([, name]) => title.includes(name))
  const longest = named.sort((a, b) => b[1].length - a[1].length)[0]
  if (longest) return longest[0]
  const words = title.replace(/(?:审核)?规则$/, '').replace(/^[\d-]+\s*(?:规则-?)?/, '').replace(/[（(].*?[)）]/g, '').trim()
  const ending = standardTypes.filter(type => words && type.endsWith(words))
  return ending.length === 1 ? ending[0] : words || title
}

/**
 * A customer's product checkers as the checker catalog lists them (path `li/L01`, key `理想:L01`).
 * This is the system template when the tenant's rule list does not carry the presets. A directory's
 * customer comes from a leaf key in that directory; a directory without any key names no customer.
 */
function catalogTemplate(source: CoverageSource, customer: string): RuleTemplate | null {
  const leaves = source.catalog?.predefined ?? []
  const owners = new Map<string, string>()
  for (const leaf of leaves) if (leaf.suite_key?.includes(':')) owners.set(leaf.path.split('/')[0], leaf.suite_key.split(':')[0])
  const name = [...new Set(owners.values())].find(owner => sameCustomer(owner, customer))
  if (!name) return null
  const rows = leaves.filter(leaf => leaf.availability !== 'historical' && owners.get(leaf.path.split('/')[0]) === name).map(leaf => {
    const type = leafType(leaf.title, source.types)
    return {
      type, label: typeLabel(type, leaf.title, source.types), ruleTitle: leaf.title, version: '', clauses: leaf.clause_count ?? null,
      target: {kind: 'system' as const, id: leaf.path},
      automation: leaf.availability === 'installed' ? automation(source.catalog, leaf.suite_key, true) : {state: 'manual' as const, note: '检查程序尚未安装'}, draft: false, updatedAt: '',
    }
  })
  // The rules page's order: standard types first, as /rule-types lists them.
  const rank = (type: string) => {const index = source.types.indexOf(type); return index < 0 ? source.types.length : index}
  rows.sort((a, b) => rank(a.type) - rank(b.type) || a.label.localeCompare(b.label, 'zh-CN'))
  return rows.length ? {key: `system:${name}`, name: `${name} 规则`, source: 'system', rows, missing: []} : null
}

/** The customer's system template: its preset rules when the tenant lists them, otherwise the product checker catalog. */
function customerTemplate(source: CoverageSource | null, customer: string): RuleTemplate | null {
  if (!source || !customer || customer === 'auto') return null
  const listed = buildTemplates({rules: source.rules, library: [], catalog: source.catalog, standardTypes: source.types}).find(row => row.source === 'system' && sameCustomer(row.name, customer))
  return listed?.rows.length ? listed : catalogTemplate(source, customer)
}

/**
 * The rules page's coverage for this customer: document types whose checker is in effect, out of the types its rules cover.
 * Nothing is guessed: without the checker catalog, or while a program state is unknown, there is no number to report.
 */
export function customerCoverage(source: CoverageSource | null, customer: string): Coverage | null {
  if (!source?.catalog) return null
  const template = customerTemplate(source, customer)
  if (!template) return null
  const value = coverage(template)
  return value.known ? {supported: value.effective, total: value.total} : null
}

export type FileSlot = {type: string; label: string}

/** Document types this customer's rules name, listed on the upload page as a hint. Automatic mode and an unknown customer have none; nothing is filled in. */
export function customerFileSlots(source: CoverageSource | null, customer: string): FileSlot[] | null {
  const template = customerTemplate(source, customer)
  if (!template) return null
  return typeGroups(template).map(group => {
    const named = documentTypeLabel(group.type)
    return {type: group.type, label: named !== group.type ? named : group.rows[0]?.label || group.type}
  })
}

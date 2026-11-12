import type {components} from '../generated/openapi'
import {buildTemplates, coverage} from './rule-templates.ts'

type Rule = components['schemas']['Rule']
/** The same backend reads as the rules page: rules, standard types and the checker catalog. */
export type CoverageSource = {rules: Rule[]; types: string[]; catalog: components['schemas']['SuiteCatalog'] | null}
export type Coverage = {supported: number; total: number}

const normalized = (value: string) => value.trim().toLowerCase().replace(/[（(].*?[)）]/g, '').replace(/汽车$/, '')
/** A customer is named by the person's picker ("理想") and by a rule ("理想汽车"); the shorter name must lead the longer one. */
export function sameCustomer(left: string, right: string): boolean {
  const a = normalized(left), b = normalized(right)
  return Boolean(a && b) && (a === b || a.startsWith(b) || b.startsWith(a))
}

/**
 * The rules page's coverage for this customer: document types whose checker is in effect, out of the types its rules cover.
 * Nothing is guessed: without the checker catalog, or while a program state is unknown, there is no number to report.
 */
export function customerCoverage(source: CoverageSource | null, customer: string): Coverage | null {
  if (!source?.catalog || !customer || customer === 'auto') return null
  const template = buildTemplates({rules: source.rules, library: [], catalog: source.catalog, standardTypes: source.types}).find(row => sameCustomer(row.name, customer))
  if (!template) return null
  const value = coverage(template)
  return value.known ? {supported: value.effective, total: value.total} : null
}

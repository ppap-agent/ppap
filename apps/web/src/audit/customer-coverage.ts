import {registeredCustomer, type RegisteredCustomer} from './customer-register.ts'
import {ruleCoverage, type Coverage} from './rule-templates.ts'

/** The customer register as GET /audit-customers returns it; null until read. */
export type CoverageSource = {customers?: RegisteredCustomer[] | null}

/** The registered customer behind a picker value; automatic mode and an unregistered name have none. */
const customerOf = (source: CoverageSource | null, customer: string) => customer && customer !== 'auto' ? registeredCustomer(source?.customers, customer) : undefined

/** The picked customer's rule coverage; none before the register is read, for automatic mode or an unregistered name. */
export function customerCoverage(source: CoverageSource | null, customer: string): Coverage | null {
  return ruleCoverage(customerOf(source, customer))
}

/** True only when the register was read and this registered customer has no type set M; its coverage is then shown as not configured. */
export function customerTypeSetMissing(source: CoverageSource | null, customer: string): boolean {
  const found = customerOf(source, customer)
  return Boolean(found && !found.coverage?.types.length)
}

export type FileSlot = {type: string; label: string}

/** The document types of this customer's type set M, listed on the upload page as a hint. Automatic mode, an unknown customer or a missing M have none. */
export function customerFileSlots(source: CoverageSource | null, customer: string): FileSlot[] | null {
  const types = customerOf(source, customer)?.coverage?.types
  return types?.length ? types.map(type => ({type: type.rule_type, label: type.name})) : null
}

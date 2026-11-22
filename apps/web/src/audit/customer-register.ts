import type {components} from '../generated/openapi'

/** A registered customer as GET /audit-customers lists it: id, full and short name, and its type set M with both coverage numerators. */
export type RegisteredCustomer = components['schemas']['AuditCustomer']

/** The registered customer a recorded value names exactly (full or short name); nothing is matched by prefix, brackets or case. */
export function registeredCustomer(customers: readonly RegisteredCustomer[] | null | undefined, value: string | null | undefined): RegisteredCustomer | undefined {
  const text = value?.trim()
  return text ? customers?.find(customer => customer.full_name === text || customer.short_name === text) : undefined
}

/** The full name for display areas that need it; an unregistered value is shown as recorded. */
export const customerFullName = (customers: readonly RegisteredCustomer[] | null | undefined, value: string | null | undefined) => registeredCustomer(customers, value)?.full_name ?? value?.trim() ?? ''

/** The short name for narrow areas; an unregistered value is shown as recorded. */
export const customerShortName = (customers: readonly RegisteredCustomer[] | null | undefined, value: string | null | undefined) => registeredCustomer(customers, value)?.short_name ?? value?.trim() ?? ''

/** A document type's name as the register lists it (by PPAP code or rule type); before the register is read, or for an unlisted type, it is shown as recorded. */
export function documentTypeName(customers: readonly RegisteredCustomer[] | null | undefined, type: string): string {
  for (const customer of customers ?? []) {
    const found = customer.coverage?.types.find(entry => entry.code === type || entry.rule_type === type)
    if (found) return found.name
  }
  return type
}

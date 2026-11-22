import type {components} from '../generated/openapi'

/** The customer register as GET /audit-customers returns it: the customers, and the one name of every document type code. */
export type CustomerCatalog = components['schemas']['AuditCustomerCatalog']
/** A registered customer: id, the one name shown, the key rules carry and a customer choice submits, and its type set M with both coverage numerators. */
export type RegisteredCustomer = components['schemas']['AuditCustomer']
export type TypeNames = CustomerCatalog['type_names']

/** The registered customer whose rule set a key names (a task's choice, a rule's customers); a company name a file writes is never looked up. */
export const registeredCustomer = (customers: readonly RegisteredCustomer[] | null | undefined, key: string | null | undefined): RegisteredCustomer | undefined =>
  key ? customers?.find(customer => customer.key === key) : undefined

/** The name people read for a chosen rule set: the registered name by its key; an enterprise's own rule set is named by itself. */
export const customerName = (customers: readonly RegisteredCustomer[] | null | undefined, key: string) => registeredCustomer(customers, key)?.name ?? key

/** A document type's name from the register's one table; before the register is read, or for an unlisted type, it is shown as recorded. */
export const documentTypeName = (typeNames: TypeNames | null | undefined, type: string) => typeNames?.[type] ?? type

/** Sample photos are checked only by this customer's U13 preset; the customer is named by its key, as a choice submits it. */
export const photoScope = {customer_preference: 'UAES', preset_key: 'U13'} as const

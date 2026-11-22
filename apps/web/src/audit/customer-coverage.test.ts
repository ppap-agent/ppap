import assert from 'node:assert/strict'
import test from 'node:test'
import {customerCoverage, customerFileSlots, customerTypeSetMissing} from './customer-coverage.ts'
import {customerName, documentTypeName, registeredCustomer, type RegisteredCustomer} from './customer-register.ts'

const customer = (id: string, key: string, name: string, types: [string, string][] | null, rules: string[] = [], templates: string[] = []): RegisteredCustomer => ({
  customer_id: id, key, name,
  coverage: types && {version: 'V3.33', effective_from: '2026-11-18', types: types.map(([code, rule_type]) => ({code, rule_type})), template_types: templates, rule_types: rules},
})
const customers = [
  customer('uaes', 'UAES', '联电科技/UAES', [['T11', '控制计划'], ['E07', 'E07']], ['T11', 'E07'], ['T11']),
  customer('nio', '蔚来', '蔚来/Nio', null),
]
const type_names = {T11: '控制计划', E07: '生产件样品照片', E12: '采购件质量计划（PGQP1）'}

test('a rule set key reads as its registered name; a company name a file writes is never looked up', () => {
  assert.equal(registeredCustomer(customers, 'UAES')?.customer_id, 'uaes')
  for (const written of ['联电科技/UAES', '联合汽车电子有限公司', ' UAES ', 'uaes']) assert.equal(registeredCustomer(customers, written), undefined)
  assert.equal(customerName(customers, 'UAES'), '联电科技/UAES')
  assert.equal(customerName(customers, '蔚来'), '蔚来/Nio')
  // An enterprise's own rule set is named by itself.
  assert.equal(customerName(customers, '长城汽车'), '长城汽车')
})

test('a type code reads as its one name, counterparts included; an unlisted type or an unread register shows it as recorded', () => {
  assert.equal(documentTypeName(type_names, 'E12'), '采购件质量计划（PGQP1）')
  assert.equal(documentTypeName(type_names, '控制计划'), '控制计划')
  assert.equal(documentTypeName(null, 'E07'), 'E07')
})

test('the picker shows the picked customer coverage; nothing before the read, before a pick, for an unregistered name or without M', () => {
  assert.deepEqual(customerCoverage({customers, type_names}, 'UAES'), {covered: 2, total: 2})
  assert.equal(customerCoverage({customers, type_names}, ''), null)
  assert.equal(customerCoverage(null, 'UAES'), null)
  assert.equal(customerCoverage({customers, type_names}, '长城'), null)
  // A missing M is a known gap, not a fallback to another total.
  assert.equal(customerCoverage({customers, type_names}, '蔚来'), null)
  assert.equal(customerTypeSetMissing({customers, type_names}, '蔚来'), true)
  assert.equal(customerTypeSetMissing({customers, type_names}, 'UAES'), false)
  assert.equal(customerTypeSetMissing(null, '蔚来'), false)
})

test('the upload hint lists the types of the customer type set M by their names', () => {
  assert.deepEqual(customerFileSlots({customers, type_names}, 'UAES'), [{type: '控制计划', label: '控制计划'}, {type: 'E07', label: '生产件样品照片'}])
  assert.equal(customerFileSlots({customers, type_names}, '蔚来'), null)
  assert.equal(customerFileSlots({customers, type_names}, ''), null)
  assert.equal(customerFileSlots(null, 'UAES'), null)
})

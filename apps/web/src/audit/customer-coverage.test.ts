import assert from 'node:assert/strict'
import test from 'node:test'
import {customerCoverage, customerFileSlots, customerTypeSetMissing} from './customer-coverage.ts'
import {customerFullName, customerShortName, registeredCustomer, type RegisteredCustomer} from './customer-register.ts'

const customer = (id: string, full: string, short: string, types: [string, string][] | null, rules: string[] = [], templates: string[] = []): RegisteredCustomer => ({
  customer_id: id, name: short, full_name: full, short_name: short,
  coverage: types && {version: 'V3.33', effective_from: '2026-11-18', types: types.map(([code, rule_type]) => ({code, rule_type})), template_types: templates, rule_types: rules},
})
const customers = [
  customer('uaes', '联合汽车电子有限公司', 'UAES', [['T11', '控制计划'], ['E07', 'E07']], ['T11', 'E07'], ['T11']),
  customer('nio', '蔚来汽车', '蔚来', null),
]

test('only registered names resolve; look-alikes are shown as recorded', () => {
  assert.equal(registeredCustomer(customers, ' UAES ')?.customer_id, 'uaes')
  assert.equal(registeredCustomer(customers, '联合汽车电子有限公司')?.customer_id, 'uaes')
  assert.equal(registeredCustomer(customers, 'UAES（联电）'), undefined)
  assert.equal(registeredCustomer(customers, 'uaes'), undefined)
  assert.equal(customerFullName(customers, 'UAES'), '联合汽车电子有限公司')
  assert.equal(customerShortName(customers, '蔚来汽车'), '蔚来')
  assert.equal(customerFullName(customers, '长城汽车'), '长城汽车')
  assert.equal(customerFullName(null, 'UAES'), 'UAES')
})

test('the picker shows the backend rule coverage N_r/M; nothing is shown before the read, for automatic mode or without M', () => {
  assert.deepEqual(customerCoverage({customers}, 'UAES'), {supported: 2, total: 2})
  assert.equal(customerCoverage({customers}, 'auto'), null)
  assert.equal(customerCoverage({customers: null}, 'UAES'), null)
  assert.equal(customerCoverage(null, 'UAES'), null)
  assert.equal(customerCoverage({customers}, '长城'), null)
  // A missing M is a known gap, not a fallback to another total.
  assert.equal(customerCoverage({customers}, '蔚来'), null)
  assert.equal(customerTypeSetMissing({customers}, '蔚来'), true)
  assert.equal(customerTypeSetMissing({customers}, 'UAES'), false)
  assert.equal(customerTypeSetMissing(null, '蔚来'), false)
})

test('the upload hint lists the types of the customer type set M', () => {
  assert.deepEqual(customerFileSlots({customers}, 'UAES'), [{type: '控制计划', label: '控制计划'}, {type: 'E07', label: '生产件样品照片'}])
  assert.equal(customerFileSlots({customers}, '蔚来'), null)
  assert.equal(customerFileSlots({customers}, 'auto'), null)
  assert.equal(customerFileSlots(null, 'UAES'), null)
})

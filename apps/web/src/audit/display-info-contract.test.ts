import {test} from 'node:test'
import {strict as assert} from 'node:assert'
import type {components} from '../generated/openapi.ts'
import {reconcileTaskDisplay} from './display-info-contract.ts'

type Task = components['schemas']['AuditTask']
const values = {customer: '已更正客户', model: '车型 A', project: '项目 A', part_number: 'P-204', part: '零件 A'}
const task = (): Task => ({confirmation_basis_digest: null, confirmation_scope: null, id: 'task-a', number: 'PPAP-A', state: 'reviewing', created_at: '', updated_at: '', creator: {name: '示例', mine: true}, source_draft_id: null, round_id: 'round-a', customer_preference: null, completion_reason: null, estimated_points: null, files: [{file: {id: 'version-a', file_id: 'file-a', original_filename: '原件.xlsx', content_type: '', byte_size: 1, status: 'ready', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: 'valid', reasons: []}, match: null, display_info: {revision: 2, values}}]})

test('task reads retain confirmed display values', () => {
  const before = task(), next = task()
  next.files[0].display_info = {revision: 1, values: {...values, customer: '较早客户'}}
  const merged = reconcileTaskDisplay(before, next)
  assert.deepEqual(merged.files[0].display_info, {revision: 2, values})
  assert.deepEqual(merged.files[0].file, next.files[0].file)
  assert.deepEqual(before.files[0].display_info, {revision: 2, values})
  delete next.files[0].display_info
  const missing = reconcileTaskDisplay(before, next)
  assert.deepEqual(missing.files[0].display_info, {revision: 2, values})
})

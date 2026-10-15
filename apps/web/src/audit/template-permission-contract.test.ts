import assert from 'node:assert/strict'
import test from 'node:test'
import {validPermissions, validPermissionReceipt, permissionUnresolved, type TemplatePermissions, type PermissionRequest} from './template-permission-contract.ts'

const folder = {customer_id: 'a', folder_id: 'folder-a'}
const member = {target: {kind: 'user' as const, id: 'wang'}, display_name: 'Wang', role: 'edit', source: 'direct' as const}
const action = {id: 'a1', target: member.target, target_name: member.display_name, before: 'edit', after: 'view', label: 'View'}
const snapshot: TemplatePermissions = {folder, permission_revision: 'acl-1', query: '', creator_name: 'Not the current user', manage_access: {state: 'allowed', reason: null}, members: [member], actions: [action]}
const request: PermissionRequest = {request_id: 'request-1', folder, permission_revision: 'acl-1', action}

test('permission choices come from the checked folder and ACL revision, not creator display text', () => {
  assert.equal(validPermissions(snapshot, folder, ''), true)
  for (const change of [{folder: {...folder, customer_id: 'b'}}, {permission_revision: ''}, {query: 'other'}, {members: [member, member]}, {actions: [action, action]}, {members: [{...member, source: 'owner' as const}]}, {members: [{...member, source: 'inherited' as const}]}, {manage_access: {state: 'denied' as const, reason: null}}, {actions: [{...action, before: 'other'}]}, {actions: [{...action, target: {...member.target, id: ''}}]}]) assert.equal(validPermissions({...snapshot, ...change}, folder, ''), false)
  assert.equal(validPermissions({...snapshot, manage_access: {state: 'denied', reason: null}, actions: []}, folder, ''), true)
})

test('only the exact original permission receipt confirms a mutation, never a matching ACL', () => {
  const receipt = {request, state: 'applied' as const, result_revision: 'acl-2', reason: null}
  assert.equal(validPermissionReceipt(receipt, request), true)
  for (const change of [{result_revision: ''}, {result_revision: 'acl-1'}, {request: {...request, request_id: 'other'}}, {request: {...request, permission_revision: 'other'}}, {request: {...request, action: {...action, after: 'other'}}}, {request: {...request, folder: {...folder, folder_id: 'other'}}}]) assert.equal(validPermissionReceipt({...receipt, ...change}, request), false)
  for (const state of ['unknown', 'pending', 'rejected', 'conflict'] as const) {
    assert.equal(validPermissionReceipt({...receipt, state}, request), false)
    assert.equal(validPermissionReceipt({...receipt, state, result_revision: null}, request), true)
    assert.equal(permissionUnresolved({...receipt, state, result_revision: null}), ['pending', 'unknown'].includes(state))
  }
})

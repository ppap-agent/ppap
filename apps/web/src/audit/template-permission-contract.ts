import type {components} from '../generated/openapi'
import {sameSnapshot} from './result-projection.ts'
import {targetKey} from './sharing-contract.ts'

export type PermissionFolder = components['schemas']['AuditTemplatePermissionFolder']
export type TemplatePermissions = components['schemas']['AuditTemplatePermissions']
export type PermissionAction = components['schemas']['AuditTemplatePermissionAction']
export type PermissionRequest = components['schemas']['AuditTemplatePermissionRequest']
export type PermissionReceipt = components['schemas']['AuditTemplatePermissionReceipt']
export type AuditTemplatePermissionAdapter = {
  readPermissions: (folder: PermissionFolder, query: string) => Promise<TemplatePermissions>
  apply: (request: PermissionRequest) => Promise<PermissionReceipt>
  readChange: (request: PermissionRequest) => Promise<PermissionReceipt>
}
export const permissionUnresolved = (receipt: PermissionReceipt | null) => Boolean(receipt && ['pending', 'unknown'].includes(receipt.state))
export function validPermissions(snapshot: TemplatePermissions, folder: PermissionFolder, query: string) {
  if (!folder.customer_id || !folder.folder_id || !sameSnapshot(snapshot.folder, folder) || !snapshot.permission_revision || snapshot.query !== query || new Set(snapshot.members.map(row => targetKey(row.target))).size !== snapshot.members.length || new Set(snapshot.actions.map(row => row.id)).size !== snapshot.actions.length || snapshot.manage_access.state !== 'allowed' && snapshot.actions.length) return false
  if (snapshot.members.some(row => !row.target.id || !row.display_name || !row.role)) return false
  return snapshot.actions.every(action => {
    const member = snapshot.members.find(row => targetKey(row.target) === targetKey(action.target))
    return Boolean(action.id && action.target.id && action.target_name && action.label && action.after !== action.before && action.after !== '' && action.before !== '' && (member ? member.source === 'direct' && member.role === action.before : action.before === null))
  })
}
export function validPermissionReceipt(receipt: PermissionReceipt, request: PermissionRequest) {
  return sameSnapshot(receipt.request, request) && (receipt.state === 'applied' ? Boolean(receipt.result_revision && receipt.result_revision !== request.permission_revision) : receipt.result_revision === null)
}

import type {components} from '../generated/openapi'
import {sameSnapshot} from './result-projection.ts'
import {templateKey, type TemplateFolder, type TemplateZip} from './template-contract.ts'
import type {ShareTransport} from './sharing-contract'

export type TemplateShare = components['schemas']['AuditTemplateShare']
export type TemplateShareRequest = components['schemas']['AuditTemplateShareRequest']
export type TemplateShareScope = components['schemas']['AuditTemplateShareScope']
export type TemplateShareContent = components['schemas']['AuditTemplateShareContent']
export type AuditTemplateSharingAdapter = ShareTransport<TemplateShareRequest> & {readScope: (folder: TemplateFolder) => Promise<TemplateShareScope>}
export const templateFolderKey = (folder: Pick<TemplateFolder, 'customer_id' | 'folder_id'>) => JSON.stringify([folder.customer_id, folder.folder_id])
export const templateShareReference = (scope: TemplateShareScope): TemplateShareRequest['scope'] => ({customer_id: scope.customer_id, folder_id: scope.folder_id, folder_revision: scope.folder_revision, templates: scope.templates.map(row => structuredClone(row.reference))})
export function validTemplateShareScope(scope: TemplateShareScope, folder: TemplateFolder) {
  return Boolean(scope.customer_id === folder.customer_id && scope.folder_id === folder.folder_id && scope.folder_revision === folder.folder_revision && scope.customer_name && scope.templates.length === folder.folder_template_count && scope.templates.length > 0 && new Set(scope.templates.map(row => row.reference.template_id)).size === scope.templates.length && scope.templates.every(row => row.reference.customer_id === scope.customer_id && row.reference.template_id && row.reference.file_version_id && row.reference.file_version_id === row.file.id && row.file.status === 'ready' && row.file.byte_size > 0))
}
export function templateShareContent(scope: TemplateShareScope, mode: 'link' | 'zip', zip: TemplateZip | null): TemplateShareContent | null {
  if (!scope.templates.length || scope.templates.some(row => row.share_access.state !== 'allowed')) return null
  if (mode === 'link') return scope.link_access.state === 'allowed' ? {kind: 'link', package: null} : null
  if (scope.zip_access.state !== 'allowed' || scope.templates.some(row => row.download_access.state !== 'allowed') || !zip || !zip.complete || zip.access.state !== 'allowed' || zip.customer_id !== scope.customer_id || zip.folder_id !== scope.folder_id || zip.folder_revision !== scope.folder_revision || zip.templates.length !== scope.templates.length || new Set(zip.templates.map(row => templateKey(row.reference))).size !== scope.templates.length || !zip.byte_size) return null
  if (new Set(zip.templates.map(entry => entry.filename)).size !== zip.templates.length || zip.templates.some(entry => !entry.filename || /[/\\\u0000-\u001f]/.test(entry.filename))) return null
  if (!scope.templates.every(row => zip.templates.some(entry => sameSnapshot(entry.reference, row.reference) && entry.byte_size === row.file.byte_size))) return null
  return {kind: 'zip', package: structuredClone(zip)}
}

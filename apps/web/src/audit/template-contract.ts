import type {components} from '../generated/openapi'

export type TemplateReference = components['schemas']['AuditTemplateReference']
export type TemplateItem = components['schemas']['AuditTemplateItem']
export type TemplatePage = components['schemas']['AuditTemplatePage']
export type TemplateQuery = components['schemas']['AuditTemplateQuery']
export type TemplateCatalog = components['schemas']['AuditTemplateCatalog']
export type TemplateZip = components['schemas']['AuditTemplateZip']
export type TemplateFolder = Pick<TemplatePage, 'folder_id' | 'folder_revision' | 'folder_template_count'> & {customer_id: string}
export type TemplateContent = {reference: TemplateReference; blob: Blob}
export type AuditTemplateAdapter = {
  readCatalog: () => Promise<TemplateCatalog>
  listTemplates: (query: TemplateQuery) => Promise<TemplatePage>
  readFile: (item: TemplateItem, purpose: 'preview' | 'download') => Promise<TemplateContent>
  readZip: (folder: TemplateFolder) => Promise<{snapshot: TemplateZip; blob: Blob}>
}
export type AuditSupportAdapter = {
  read: () => Promise<components['schemas']['AuditSupportInfo']>
  readQr: (configVersion: string) => Promise<Blob>
}
export const templateKey = (reference: TemplateReference) => JSON.stringify([reference.customer_id, reference.template_id, reference.file_version_id])
export const coveredTypes = (catalog: TemplateCatalog, types: string[]) => [...new Set(types)].filter(type => catalog.coverage_universe.includes(type))
const safeZipName = (name: string) => Boolean(name && name !== '.' && name !== '..' && !/[/\\\u0000-\u001f]/.test(name))

export function validCatalog(catalog: TemplateCatalog) {
  return catalog.coverage_universe.length === 17 && new Set(catalog.coverage_universe).size === 17 && catalog.coverage_universe.every(Boolean) && new Set(catalog.customers.map(row => row.customer_id)).size === catalog.customers.length && catalog.customers.every(row => row.customer_id && row.name && coveredTypes(catalog, row.effective_rule_types).length > 0)
}
export function validTemplatePage(page: TemplatePage, query: TemplateQuery) {
  const sameQuery = page.query.customer_id === query.customer_id && page.query.search === query.search && page.query.page === query.page && page.query.page_size === query.page_size
  const validCount = Number.isInteger(page.total) && page.total >= 0 && Number.isInteger(page.folder_template_count) && page.folder_template_count >= page.total && page.items.length === Math.min(10, Math.max(0, page.total - (query.page - 1) * 10))
  const fixedVersions = page.items.every(row => row.reference.customer_id === query.customer_id && row.reference.template_id && row.reference.file_version_id === row.file.id && row.file.status === 'ready' && row.file.byte_size > 0 && Number.isFinite(Date.parse(row.updated_at)))
  return Boolean(sameQuery && page.folder_id && page.folder_revision && validCount && new Set(page.items.map(row => row.reference.template_id)).size === page.items.length && fixedVersions && page.items.every((row, index, rows) => !index || Date.parse(row.updated_at) <= Date.parse(rows[index - 1].updated_at)))
}
export async function readTemplateFile(adapter: AuditTemplateAdapter, item: TemplateItem, purpose: 'preview' | 'download') {
  if (item.file.status !== 'ready' || item[`${purpose}_access`].state !== 'allowed') throw new Error('template_access_denied')
  const result = await adapter.readFile(structuredClone(item), purpose)
  if (templateKey(result.reference) !== templateKey(item.reference) || result.blob.size !== item.file.byte_size || result.blob.type !== item.file.content_type) throw new Error('template_content_mismatch')
  return result.blob
}
export async function readTemplateZip(adapter: AuditTemplateAdapter, folder: TemplateFolder) {
  const {snapshot, blob} = await adapter.readZip(structuredClone(folder))
  const entries = snapshot.templates
  if (snapshot.access.state !== 'allowed' || !snapshot.complete || snapshot.customer_id !== folder.customer_id || snapshot.folder_id !== folder.folder_id || snapshot.folder_revision !== folder.folder_revision || entries.length !== folder.folder_template_count || !entries.length || new Set(entries.map(row => templateKey(row.reference))).size !== entries.length || new Set(entries.map(row => row.filename)).size !== entries.length || entries.some(row => row.reference.customer_id !== folder.customer_id || !row.reference.template_id || !row.reference.file_version_id || !safeZipName(row.filename) || !Number.isInteger(row.byte_size) || row.byte_size <= 0) || blob.type !== 'application/zip' || blob.size !== snapshot.byte_size || snapshot.manifest_filename !== null && (!safeZipName(snapshot.manifest_filename) || entries.some(row => row.filename === snapshot.manifest_filename))) throw new Error('incomplete_template_zip')
  const {unzipSync} = await import('fflate')
  const members = new Map<string, number>()
  let duplicate = false
  // Inspect the central directory without inflating untrusted file contents.
  unzipSync(new Uint8Array(await blob.arrayBuffer()), {filter: file => {if (members.has(file.name)) duplicate = true; members.set(file.name, file.originalSize); return false}})
  if (duplicate || members.size !== entries.length + (snapshot.manifest_filename ? 1 : 0) || entries.some(row => members.get(row.filename) !== row.byte_size) || snapshot.manifest_filename && !members.has(snapshot.manifest_filename)) throw new Error('incomplete_template_zip')
  return {snapshot, blob}
}

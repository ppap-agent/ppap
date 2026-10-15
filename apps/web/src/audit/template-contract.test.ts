import assert from 'node:assert/strict'
import test from 'node:test'
import {coveredTypes, readTemplateFile, readTemplateZip, validCatalog, validTemplatePage, type AuditTemplateAdapter, type TemplateCatalog, type TemplateItem, type TemplatePage, type TemplateZip} from './template-contract.ts'

const allowed = {state: 'allowed' as const, reason: null}
const catalog: TemplateCatalog = {coverage_universe: Array.from({length: 17}, (_, index) => `type-${index}`), customers: [{customer_id: 'customer-a', name: 'A', effective_rule_types: ['type-0', 'type-0', 'type-1']}]}
const reference = {customer_id: 'customer-a', template_id: 'template-1', file_version_id: 'v1'}
const item: TemplateItem = {reference, file: {id: 'v1', file_id: 'template-1', original_filename: 'a.xlsx', content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', byte_size: 3, status: 'ready', sha256: null}, document_type: 'type-0', updated_at: '2026-10-16T00:00:00Z', preview_access: allowed, download_access: allowed, share_access: allowed}
const page: TemplatePage = {query: {customer_id: 'customer-a', search: '', page: 1, page_size: 10}, folder_id: 'folder-a', folder_revision: 'rev-1', folder_template_count: 1, creator_name: null, manage_access: allowed, zip_access: allowed, items: [item], total: 1}
const zip: TemplateZip = {customer_id: 'customer-a', folder_id: 'folder-a', folder_revision: 'rev-1', templates: [reference], filename: 'a.zip', byte_size: 3, complete: true, access: allowed}
const folder = {customer_id: 'customer-a', folder_id: 'folder-a', folder_revision: 'rev-1', folder_template_count: 1}
const adapter: AuditTemplateAdapter = {readCatalog: async () => catalog, listTemplates: async () => page, readFile: async () => ({reference, blob: new Blob(['abc'], {type: item.file.content_type})}), readZip: async () => ({snapshot: zip, blob: new Blob(['PK!'], {type: 'application/zip'})})}

test('template coverage counts a union within an explicit 17-type universe, never attachments', () => {
  assert.equal(validCatalog(catalog), true)
  assert.deepEqual(coveredTypes(catalog, ['type-0', 'type-0', 'type-1', 'not-in-dictionary']), ['type-0', 'type-1'])
  assert.equal(validCatalog({...catalog, coverage_universe: [...catalog.coverage_universe, 'type-18']}), false)
  assert.equal(validCatalog({...catalog, customers: [{...catalog.customers[0], effective_rule_types: []}]}), false)
  assert.equal(validCatalog({...catalog, customers: [...catalog.customers, catalog.customers[0]]}), false)
})

test('template pages bind query, customer, fixed versions and date order', () => {
  assert.equal(validTemplatePage(page, page.query), true)
  for (const query of [{...page.query, customer_id: 'b'}, {...page.query, search: 'b'}, {...page.query, page: 2}]) assert.equal(validTemplatePage(page, query), false)
  assert.equal(validTemplatePage({...page, items: [{...item, reference: {...reference, customer_id: 'b'}}]}, page.query), false)
  assert.equal(validTemplatePage({...page, items: [{...item, file: {...item.file, id: 'v2'}}]}, page.query), false)
  assert.equal(validTemplatePage({...page, items: [{...item, updated_at: 'unknown'}]}, page.query), false)
})

test('template reads reject unknown permissions and bytes for another version', async () => {
  assert.equal((await readTemplateFile(adapter, item, 'preview')).size, 3)
  for (const state of ['denied', 'unknown', 'deleted'] as const) await assert.rejects(readTemplateFile(adapter, {...item, preview_access: {state, reason: null}}, 'preview'))
  await assert.rejects(readTemplateFile({...adapter, readFile: async () => ({reference: {...reference, file_version_id: 'v2'}, blob: new Blob(['abc'], {type: item.file.content_type})})}, item, 'preview'))
  await assert.rejects(readTemplateFile({...adapter, readFile: async () => ({reference, blob: new Blob(['abc'], {type: 'text/html'})})}, item, 'download'))
})

test('template ZIPs require the same complete folder revision, distinct versions and actual ZIP bytes', async () => {
  assert.equal((await readTemplateZip(adapter, folder)).blob.size, 3)
  for (const change of [{complete: false}, {customer_id: 'b'}, {folder_id: 'b'}, {folder_revision: 'rev-2'}, {templates: []}, {templates: [reference, reference]}, {byte_size: 2}, {access: {state: 'unknown' as const, reason: null}}]) await assert.rejects(readTemplateZip({...adapter, readZip: async () => ({snapshot: {...zip, ...change}, blob: new Blob(['PK!'], {type: 'application/zip'})})}, folder))
  await assert.rejects(readTemplateZip({...adapter, readZip: async () => ({snapshot: zip, blob: new Blob(['bad'], {type: 'application/zip'})})}, folder))
})

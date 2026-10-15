import assert from 'node:assert/strict'
import test from 'node:test'
import {validTemplateShareScope, type TemplateShareScope} from './template-sharing-contract.ts'

const allowed = {state: 'allowed' as const, reason: null}
const item = {reference: {customer_id: 'a', template_id: 'one', file_version_id: 'v1'}, file: {file_id: 'one', id: 'v1', original_filename: 'one.xlsx', content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', byte_size: 3, status: 'ready', sha256: null}, document_type: '控制计划', updated_at: '2026-10-16T00:00:00Z', preview_access: allowed, download_access: allowed, share_access: allowed}
const folder = {customer_id: 'a', folder_id: 'folder-a', folder_revision: 'r1', folder_template_count: 1}
const scope: TemplateShareScope = {...folder, customer_name: 'A', templates: [item], link_access: allowed, zip_access: allowed}

test('complete template sharing requires distinct templates and nonempty version identities', () => {
  assert.equal(validTemplateShareScope(scope, folder), true)
  assert.equal(validTemplateShareScope({...scope, templates: [item, {...item, reference: {...item.reference, file_version_id: 'v2'}, file: {...item.file, id: 'v2'}}]}, {...folder, folder_template_count: 2}), false)
  assert.equal(validTemplateShareScope({...scope, templates: [{...item, reference: {...item.reference, file_version_id: ''}, file: {...item.file, id: ''}}]}, folder), false)
})

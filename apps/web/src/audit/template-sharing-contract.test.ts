import assert from 'node:assert/strict'
import test from 'node:test'
import {templateShareContent, templateShareReference, validTemplateShareRecovery, validTemplateShareScope, type TemplateShareRecovery, type TemplateShareScope} from './template-sharing-contract.ts'

const allowed = {state: 'allowed' as const, reason: null}
const item = {reference: {customer_id: 'a', template_id: 'one', file_version_id: 'v1'}, file: {file_id: 'one', id: 'v1', original_filename: 'one.xlsx', content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', byte_size: 3, status: 'ready', sha256: null}, document_type: '控制计划', updated_at: '2026-10-16T00:00:00Z', preview_access: allowed, download_access: allowed, share_access: allowed}
const folder = {customer_id: 'a', folder_id: 'folder-a', folder_revision: 'r1', folder_template_count: 1}
const scope: TemplateShareScope = {...folder, customer_name: 'A', templates: [item], link_access: allowed, zip_access: allowed}

test('complete template sharing requires distinct templates and nonempty version identities', () => {
  assert.equal(validTemplateShareScope(scope, folder), true)
  assert.equal(validTemplateShareScope({...scope, templates: [item, {...item, reference: {...item.reference, file_version_id: 'v2'}, file: {...item.file, id: 'v2'}}]}, {...folder, folder_template_count: 2}), false)
  assert.equal(validTemplateShareScope({...scope, templates: [{...item, reference: {...item.reference, file_version_id: ''}, file: {...item.file, id: ''}}]}, folder), false)
})

test('a complete ZIP can disambiguate duplicate original filenames without changing fixed references', () => {
  const second = {...item, reference: {...item.reference, template_id: 'two', file_version_id: 'v2'}, file: {...item.file, file_id: 'two', id: 'v2'}}
  const selected = {...scope, templates: [item, second]}
  const zip = {...folder, templates: [item, second].map((row, index) => ({reference: row.reference, filename: index ? 'one (two).xlsx' : 'one.xlsx', byte_size: row.file.byte_size})), manifest_filename: null, filename: 'templates.zip', byte_size: 200, complete: true, access: allowed}
  assert.deepEqual(templateShareContent(selected, 'zip', zip), {kind: 'zip', package: zip})
  assert.equal(templateShareContent(selected, 'zip', {...zip, templates: zip.templates.map(row => ({...row, filename: 'same.xlsx'}))}), null)
})

test('restoration binds the original customer, captured scope and receipt rather than the current directory', () => {
  const target = {kind: 'user' as const, id: 'reader', display_name: 'Reader', organization_id: 'org', eligibility: 'eligible' as const, reason: null}
  const recovery: TemplateShareRecovery = {scope, share: {request: {request_id: 'original-request', organization_id: 'org', scope: templateShareReference(scope), targets: [{kind: 'user', id: 'reader'}], content: {kind: 'link', package: null}}, receipt: {request_id: 'original-request', state: 'accepted', reason: null}, deliveries: [{target, attempt_id: 'original-request', state: 'unknown', reason: null, retry_allowed: false}]}}
  assert.equal(validTemplateShareRecovery(recovery, 'a'), true)
  assert.equal(validTemplateShareRecovery(recovery, 'other-customer'), false)
  assert.equal(validTemplateShareRecovery({...recovery, scope: {...scope, folder_revision: 'current-but-unrelated'}}, 'a'), false)
  assert.equal(validTemplateShareRecovery({...recovery, share: {...recovery.share, receipt: {...recovery.share.receipt, request_id: 'another-request'}}}, 'a'), false)
})

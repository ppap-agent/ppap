import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi'
import {historyReportReady, historyShareMatches, readArchiveAddress, resultKey, type HistoryEntry} from './archive-contract.ts'

const reference = {task_id: 'task-1', round_id: 'round-1', file_version_id: 'version-1', result_attempt: 2}
const entry: HistoryEntry = {document_id: 'document-1', reference, sequence: 3, audited_at: '', initiator: '示例', execution: 'completed', conclusion: 'PASS', source_revision: 'B', report_access: {state: 'allowed', reason: null}, report: {report_id: 'report-1', state: 'ready', reason: null, generated_at: '2026-10-16T00:00:00Z', artifact: {artifact_id: 'pdf-1', filename: '样例.pdf', content_type: 'application/pdf', byte_size: 10}}}

test('archive addresses preserve the complete result identity and reject ambiguous reports', () => {
  const query = new URLSearchParams({archiveDoc: 'document-1', auditRef: resultKey(reference), archiveReport: '1'})
  assert.deepEqual(readArchiveAddress(query.toString()), {documentId: 'document-1', reference, report: true, invalid: false})
  for (const raw of ['', 'null', '[]', '["t","r","v",0]', '["t","r","v",1.5]', '["","r","v",1]', '{"task_id":"task-1"}']) assert.equal(readArchiveAddress(new URLSearchParams({archiveDoc: 'document-1', auditRef: raw}).toString()).invalid, true)
  assert.equal(readArchiveAddress('archiveReport=1').invalid, true)
})

test('an explicit historical result requires its document identity', () => {
  const cases: Record<string, string>[] = [{auditRef: resultKey(reference)}, {archiveDoc: '', auditRef: resultKey(reference)}, {auditRef: resultKey(reference), archiveReport: '1'}]
  for (const fields of cases) {
    const address = readArchiveAddress(new URLSearchParams(fields).toString())
    assert.equal(address.invalid, true)
    assert.deepEqual(address.reference, reference)
  }
  assert.deepEqual(readArchiveAddress(''), {documentId: null, reference: null, invalid: false, report: false})
  assert.deepEqual(readArchiveAddress('archiveDoc=document-1'), {documentId: 'document-1', reference: null, invalid: false, report: false})
})

test('history report availability does not imply original or private-task access', () => {
  assert.equal(historyReportReady(entry), true)
  assert.equal(historyReportReady({...entry, execution: 'running'}), false)
  assert.equal(historyReportReady({...entry, conclusion: 'UNKNOWN'}), true)
  assert.equal(historyReportReady({...entry, conclusion: null}), false)
  assert.equal(historyReportReady({...entry, report_access: {state: 'denied', reason: null}}), false)
  assert.equal(historyReportReady({...entry, report: {...entry.report, artifact: null}}), false)
})

test('history never inherits an unrelated or multi-report task share', () => {
  const share: components['schemas']['AuditReportShare'] = {request: {request_id: 'share-1', organization_id: 'organization-1', reports: [{...reference, report_id: 'report-1'}], targets: [], content: {kind: 'link', artifact: null, package_id: null}}, receipt: {request_id: 'share-1', state: 'pending', reason: null}, deliveries: []}
  assert.equal(historyShareMatches(entry, share), true)
  for (const change of [{task_id: 'task-2'}, {round_id: 'round-2'}, {file_version_id: 'version-2'}, {result_attempt: 1}, {report_id: 'report-2'}]) assert.equal(historyShareMatches(entry, {...share, request: {...share.request, reports: [{...share.request.reports[0], ...change}]}}), false)
  assert.equal(historyShareMatches(entry, {...share, request: {...share.request, reports: [...share.request.reports, share.request.reports[0]]}}), false)
  assert.equal(historyShareMatches(entry, null), true)
})

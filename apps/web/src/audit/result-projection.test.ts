import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi'
import {clauseObservation, resultConsistencyError, taskProgressError, taskResultCounts, sameFrozenInputs, progressRegressed, sameSnapshot, reportReady, allTaskReportsReady, reportReference, shareContentFor, packageOutcomeError, taskPackageScopeError, frozenResultError, reconcileTaskNotification} from './result-projection.ts'
import {readReportPdf, readReportZip, readTaskReportPackage} from './report-contract.ts'

type Task = components['schemas']['AuditTask']
function projection(): Task {
  const rule: components['schemas']['BoundRule'] = {id: 'rule-v1', title: '冻结规则', body: {kind: 'engine', type: '控制计划', rule_profile: 'v3', customers: [], models: [], parts: [], rules: [{id: '1', category: '频次', priority: null, rule_to_check: '频次须一致。', type_of_rule: 'table'}]}}
  const checks: components['schemas']['AuditClauseEvaluation'][] = [{rule_item: structuredClone(rule.body.kind === 'engine' ? rule.body.rules[0] : null!), execution: 'completed', coverage: 'executed', evidence_verified: true, scope: {checked: 1, total: 1, unknown: 0}, scope_notice: null, finding: {item_key: '1', item: '频次', status: 'PASS', classification: '已核对', suggestions: '', priority: null, locator: null, review: null}, evidence: [{file_version_id: 'file-v1', locator: {kind: 'xlsx_cell', sheet: '审核', cell: 'C12'}, excerpt: '每班', location_gap: null, rendition: 'original', derived_file_version_id: null}]}]
  return {confirmation_basis_digest: null, confirmation_scope: null, id: 'task-1', number: '001', state: 'completed', completion_reason: 'processed', analysis_mode: 'standard', source_draft_id: null, round_id: 'round-1', customer_preference: null, created_at: '', updated_at: '', estimated_points: null, files: [{file: {id: 'file-v1', file_id: 'file-1', original_filename: '控制计划.xlsx', content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', byte_size: 1024, status: 'ready', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: 'valid', reasons: []}, match: {state: 'matched', metadata: {customer: null, model: null, part: null}, document_type: '控制计划', rule: structuredClone(rule), gaps: []}, audit: {file_version_id: 'file-v1', round_id: 'round-1', result_attempt: 1, rule, execution: 'completed', progress: {completed: 1, total: 1}, failure: null, conclusion: 'PASS', checks, counts: {pass_count: 1, ng_count: 0, unknown_count: 0, not_covered_count: 0, error_count: 0}, report: {state: 'ready', report_id: 'report-1', reason: null}}}]}
}
const error = (task: Task) => resultConsistencyError(task, task.files[0])

test('the journey is complete only when every audited file and its report are ready', () => {
  const task = projection(), report = task.files[0].audit!.report
  assert.equal(allTaskReportsReady(task), false)
  report.generated_at = '2026-10-20T02:00:00Z'
  report.artifact = {artifact_id: 'pdf-1', filename: '控制计划.xlsx-审核报告.pdf', content_type: 'application/pdf', byte_size: 9}
  assert.equal(allTaskReportsReady(task), true)
  const reference = structuredClone(task.files[0]); reference.match!.state = 'reference'; reference.match!.rule = null; reference.audit = null
  task.files.push(reference)
  assert.equal(allTaskReportsReady(task), true)
  report.state = 'failed'
  assert.equal(allTaskReportsReady(task), false)
  report.state = 'generating'
  assert.equal(allTaskReportsReady(task), false)
  report.state = 'ready'
  task.state = 'reviewing'
  assert.equal(allTaskReportsReady(task), false)
  task.state = 'completed'; task.files = [reference]
  assert.equal(allTaskReportsReady(task), false)
})

test('workbench totals count valid audit targets, not reference or excluded files', () => {
  const task = projection(), ng = structuredClone(task.files[0])
  ng.file.id = 'file-v2'; ng.audit!.file_version_id = ng.file.id
  ng.audit!.checks[0].finding!.status = 'NG'; ng.audit!.counts = {...ng.audit!.counts, pass_count: 0, ng_count: 1}
  ng.audit!.conclusion = 'NG'
  task.files.push(ng)
  const reference = structuredClone(task.files[0]); reference.file.id = 'reference-v1'; reference.match!.state = 'reference'; reference.match!.rule = null; delete reference.audit
  const excluded = structuredClone(reference); excluded.file.id = 'excluded-v1'; excluded.selection = 'unselected'
  task.files.push(reference, excluded)
  assert.deepEqual(taskResultCounts(task), {items: 2, pass_count: 1, ng_count: 1, unknown_count: 0, not_covered_count: 0, error_count: 0})
  ng.audit!.checks[0].evidence = []; ng.audit!.checks[0].evidence_verified = false
  assert.equal(taskResultCounts(task)?.ng_count, 1)
  assert.equal(task.files[0].audit!.conclusion, 'PASS')
})

test('workbench does not publish aggregate results from missing or conflicting projections', () => {
  const task = projection()
  task.files[0].audit!.counts.pass_count = 7
  assert.equal(taskResultCounts(task), null)
  delete task.files[0].audit
  assert.equal(taskResultCounts(task), null)
  task.state = 'reviewing'; task.completion_reason = null
  assert.equal(taskResultCounts(task), null)
})

test('missing optional evidence does not downgrade executed PASS or NG or block its report', () => {
  for (const status of ['PASS', 'NG'] as const) {
    const task = projection(), audit = task.files[0].audit!, check = audit.checks[0]
    check.finding!.status = status
    check.evidence_verified = false; check.scope = null; check.evidence = []
    check.scope_notice = '未提供具体定位与摘录。'
    audit.conclusion = status
    audit.counts = {pass_count: status === 'PASS' ? 1 : 0, ng_count: status === 'NG' ? 1 : 0, unknown_count: 0, not_covered_count: 0, error_count: 0}
    assert.equal(error(task), '')
    assert.ok(reportReference(task, task.files[0]))
  }
})

test('uncovered clauses explain the next human action without exposing checker codes', () => {
  const check = projection().files[0].audit!.checks[0]
  check.finding = null
  check.coverage = 'unsupported'
  for (const reason of ['manual-only', 'unsupported']) assert.equal(clauseObservation({...check, scope_notice: reason}), '本条要求未完成自动检查，请核对原件并记录人工复核。')
  assert.equal(clauseObservation({...check, scope_notice: '来源证据与本条冻结规则不一致。'}), '来源证据与本条冻结规则不一致。')
  assert.equal(clauseObservation({...check, execution: 'running', scope_notice: 'manual-only'}), '本项审核中…')
  assert.equal(clauseObservation({...check, finding: {item_key: '1', item: '频次', status: 'PASS', classification: '已执行检查', suggestions: ''}, scope_notice: '来源证据与本条冻结规则不一致。'}), '已执行检查；来源证据与本条冻结规则不一致。')
})

test('notification preferences have their own revision and cannot leak across tasks', () => {
  const current = {...projection(), notification_enabled: false, notification_revision: 2}
  const old = {...projection(), notification_enabled: true, notification_revision: 1}
  assert.equal(reconcileTaskNotification(current, old).notification_enabled, false)
  assert.equal(reconcileTaskNotification(current, {...old, notification_revision: 3}).notification_enabled, true)
  assert.equal(reconcileTaskNotification(current, {...old, notification_revision: 2}).notification_enabled, undefined)
  assert.equal(reconcileTaskNotification(current, {...old, id: 'another-task'}).notification_enabled, true)
  assert.equal(reconcileTaskNotification(current, projection()).notification_enabled, undefined)
})

test('a late snapshot cannot erase or replace a newer human review in the same frozen round', () => {
  const current = projection(), old = structuredClone(current)
  const review = {judgment: 'disagree', rationale: '人工记录', next_step: '', version: 2, citations: []}
  current.files[0].audit!.checks[0].review = review
  assert.equal(progressRegressed(current, old), true)
  old.files[0].audit!.checks[0].review = {...review, version: 1}
  assert.equal(progressRegressed(current, old), true)
  old.files[0].audit!.checks[0].review = review
  assert.equal(progressRegressed(current, old), false)
  current.files[0].audit!.unmapped_findings = [{...current.files[0].audit!.checks[0].finding!, item_key: 'raw', review}]
  assert.equal(progressRegressed(current, old), true)
})

test('snapshot equality ignores property order, not values or array order', () => {
  assert.equal(sameSnapshot({a: 1, b: [{c: null}]}, {b: [{c: null}], a: 1}), true)
  assert.equal(sameSnapshot({a: 1}, {a: 2}), false)
  assert.equal(sameSnapshot([1, 2], [2, 1]), false)
  assert.equal(sameSnapshot({a: undefined}, {}), false)
})
test('a complete frozen result can show its report', () => assert.equal(error(projection()), ''))
test('a frozen reference remains available without adding an unfinished audit or report', () => {
  const task = projection(), reference = structuredClone(task.files[0])
  reference.file.id = 'reference-v1'; reference.file.original_filename = '特殊特性清单.xlsx'
  reference.match = {...reference.match!, state: 'reference', rule: null}; reference.audit = null
  task.files.push(reference); task.progress = {completed: 1, total: 1}
  assert.equal(taskProgressError(task), '')
  assert.equal(reportReference(task, reference), null)
  assert.equal(reportReady(task, reference), false)
})
test('a historical presence result needs no invented clause and cannot earn a formal PASS', () => {
  const file = projection().files[0], audit = file.audit!
  audit.rule = {id: 'presence-rule', title: '文件存在', body: {kind: 'presence'}}
  audit.checks = []; audit.progress = {completed: 0, total: 0}; audit.counts.pass_count = 0
  audit.conclusion = 'UNKNOWN'; audit.report = {state: 'not_eligible', report_id: null, reason: null}
  const historical = {file: file.file, audit}
  assert.equal(frozenResultError('round-1', historical, ['file-v1'], audit.rule), '')
  audit.conclusion = 'PASS'
  assert.match(frozenResultError('round-1', historical, ['file-v1'], audit.rule), /结论为“符合”，但审核项结果不完整/)
  assert.match(frozenResultError('another-round', historical, ['file-v1'], audit.rule), /审核结果与本次文件或规则不一致，请刷新/)
})
test('a read cannot erase ended work or reopen a terminal file in the same frozen round', () => {
  const before = projection(), after = structuredClone(before)
  before.progress = {completed: 1, total: 1}; after.progress = {completed: 1, total: 1}
  assert.equal(progressRegressed(before, after), false)
  delete after.progress
  assert.equal(progressRegressed(before, after), true)
  after.progress = null
  assert.equal(progressRegressed(before, after), true)
  after.progress = {completed: 1, total: 1}
  after.progress.completed = 0
  assert.equal(progressRegressed(before, after), true)
  after.progress.completed = 1; after.files[0].audit!.progress.completed = 0
  assert.equal(progressRegressed(before, after), true)
  after.files[0].audit!.progress.completed = 1; after.files[0].audit!.checks[0].execution = 'running'
  assert.equal(progressRegressed(before, after), true)
  after.files[0].audit!.checks[0].execution = 'completed'; after.files[0].audit!.execution = 'running'
  assert.equal(progressRegressed(before, after), true)
  after.files[0].audit!.execution = 'completed'; before.files[0].audit!.execution = 'review_failed'
  assert.equal(progressRegressed(before, after), true)
  after.round_id = 'new-explicit-round'
  assert.equal(progressRegressed(before, after), false)
})
test('completed execution cannot hide running checks, while whole-file failure can retain unexecuted checks', () => {
  const task = projection(), audit = task.files[0].audit!
  audit.checks[0] = {...audit.checks[0], execution: 'running', coverage: 'mapped', finding: null, evidence_verified: false, scope: null, evidence: []}
  audit.progress.completed = 0; audit.conclusion = null
  audit.counts.pass_count = 0; audit.report = {state: 'not_eligible', report_id: null, reason: null}
  assert.match(error(task), /文件显示已审完，但还有审核项没结束，请刷新/)
  audit.execution = 'running'
  assert.equal(error(task), '')
  audit.execution = 'review_failed'
  audit.checks[0].execution = 'queued'
  audit.failure = {code: 'read_failed', message: '读取服务失败', retryable: false}
  assert.equal(error(task), '')
})
test('task progress checks selected file endings without invalidating another file report', () => {
  const task = projection()
  assert.equal(taskProgressError(task), '')
  task.progress = {completed: 1, total: 1}
  const removed = structuredClone(task.files[0]); removed.file.id = 'file-removed'; removed.selection = 'removed'; removed.removal_reason = '未纳入本轮'; delete removed.audit; task.files.push(removed)
  assert.equal(taskProgressError(task), '')
  for (const execution of [undefined, 'queued', 'running', 'completed'] as const) {
    const pending = structuredClone(task.files[0])
    pending.file.id = 'file-pending'
    if (execution === undefined) delete pending.audit
    else {
      const audit = pending.audit!
      audit.file_version_id = pending.file.id; audit.execution = execution
      audit.checks[0] = {...audit.checks[0], execution: 'running', coverage: 'mapped', finding: null, evidence_verified: false, scope: null, evidence: []}
      audit.progress.completed = 0; audit.conclusion = null
      audit.counts.pass_count = 0; audit.report = {state: 'not_eligible', report_id: null, reason: null}
    }
    task.files[1] = pending
    assert.match(taskProgressError(task), /还有文件没审完/)
    assert.equal(resultConsistencyError(task, task.files[0]), '')
    assert.ok(reportReference(task, task.files[0]))
  }
  task.state = 'reviewing'; task.completion_reason = null
  task.progress = {completed: 1, total: 2}
  assert.equal(taskProgressError(task), '')
  task.progress.completed = 2
  assert.match(taskProgressError(task), /任务进度与文件状态不一致，请刷新/)
  task.progress = {completed: 1, total: 3}
  assert.match(taskProgressError(task), /任务进度与文件状态不一致，请刷新/)
  delete task.progress
  assert.equal(taskProgressError(task), '')
  task.files[1].audit!.execution = 'review_failed'
  task.state = 'completed'; task.completion_reason = 'processed'; task.progress = {completed: 2, total: 2}
  task.files[0].audit!.report.state = 'generating'
  task.files[0].audit!.report.generation_request = {request_id: 'regenerate-1', state: 'accepted', reason: null}
  assert.equal(taskProgressError(task), '')
  task.round_id = null; task.completion_reason = 'no_matching_rules'
  assert.equal(taskProgressError(task), '')
})
test('task completion waits for initial reports but not an identified report regeneration', () => {
  const task = projection(), report = task.files[0].audit!.report
  for (const state of ['pending', 'generating'] as const) {
    report.state = state
    for (const receipt of [null, {request_id: '', state: 'accepted' as const, reason: null}, {request_id: 'rejected-1', state: 'rejected' as const, reason: null}]) {
      report.generation_request = receipt
      assert.match(taskProgressError(task), /还有报告在生成/)
    }
    for (const receiptState of ['pending', 'accepted'] as const) {
      report.generation_request = {request_id: 'regenerate-1', state: receiptState, reason: null}
      assert.equal(taskProgressError(task), '')
    }
  }
})
test('a finding cannot impersonate another frozen rule item', () => {
  const task = projection()
  task.files[0].audit!.checks[0].finding!.item_key = 'other-rule'
  assert.match(error(task), /审核项与本次规则不一致，请刷新/)
})
test('optional scope gaps do not replace actual PASS or NG or block a formal report', () => {
  const task = projection(), audit = task.files[0].audit!
  audit.checks[0].scope = {checked: 0, total: 1, unknown: 1}
  assert.equal(error(task), '')
  audit.conclusion = 'NG'
  audit.checks[0].finding!.status = 'NG'
  audit.counts = {...audit.counts, pass_count: 0, ng_count: 1}
  assert.equal(error(task), '')
})
test('NG and PASS both count when the second check has an optional scope gap', () => {
  const task = projection(), row = task.files[0], audit = row.audit!
  const second = structuredClone(audit.checks[0])
  second.rule_item.id = '2'; second.finding!.item_key = '2'; second.scope = {checked: 0, total: 1, unknown: 1}
  audit.checks[0].finding!.status = 'NG'; audit.checks.push(second)
  if (audit.rule.body.kind === 'engine') audit.rule.body.rules.push(structuredClone(second.rule_item))
  row.match!.rule = structuredClone(audit.rule)
  audit.progress = {completed: 2, total: 2}; audit.counts = {...audit.counts, ng_count: 1}; audit.conclusion = 'NG'; audit.report = {state: 'not_eligible', report_id: null, reason: null}
  assert.equal(error(task), '')
  assert.equal(audit.checks[0].finding!.status, 'NG')
})
test('sharing accepts only a ready frozen report and its exact PDF artifact', () => {
  const task = projection(), row = task.files[0], reference = reportReference(task, row)!
  assert.equal(shareContentFor(task, [reference], 'link'), null)
  row.audit!.report.generated_at = '2026-10-16T02:00:00Z'
  row.audit!.report.artifact = {artifact_id: 'pdf-1', filename: '控制计划.xlsx-审核报告.pdf', content_type: 'application/pdf', byte_size: 9}
  assert.deepEqual(shareContentFor(task, [reference], 'link'), {kind: 'link', artifact: null, package_id: null})
  assert.deepEqual(shareContentFor(task, [reference], 'file'), {kind: 'pdf', artifact: row.audit!.report.artifact, package_id: null})
  row.audit!.report.artifact.artifact_id = ''
  assert.equal(shareContentFor(task, [reference], 'file'), null)
  row.audit!.report.artifact.artifact_id = 'pdf-1'
  assert.equal(shareContentFor(task, [{...reference, result_attempt: 2}], 'link'), null)
  assert.equal(shareContentFor(task, [reference, reference], 'file'), null)
  row.audit!.report.state = 'failed'
  assert.equal(shareContentFor(task, [reference], 'link'), null)
})
test('multi-file sharing waits for the exact ready ZIP, not the latest package', () => {
  const task = projection(), first = task.files[0]
  first.audit!.report.generated_at = '2026-10-16T02:00:00Z'
  first.audit!.report.artifact = {artifact_id: 'pdf-1', filename: '控制计划.xlsx-审核报告.pdf', content_type: 'application/pdf', byte_size: 9}
  const second = structuredClone(first)
  second.file.id = 'file-v2'; second.audit!.file_version_id = 'file-v2'; second.audit!.report.report_id = 'report-2'
  task.files.push(second)
  const reports = task.files.map(row => reportReference(task, row)!)
  assert.equal(shareContentFor(task, reports, 'file'), null)
  task.report_package = {package_id: 'zip-1', receipt: {request_id: 'pack-1', state: 'accepted', reason: null}, reports: structuredClone(reports), state: 'ready', artifact: {artifact_id: 'zip-bytes', filename: '审核报告.zip', content_type: 'application/zip', byte_size: 99}, reason: null, retry_allowed: false}
  assert.deepEqual(shareContentFor(task, reports, 'file'), {kind: 'zip', artifact: task.report_package.artifact, package_id: 'zip-1'})
  task.report_package.artifact!.artifact_id = ''
  assert.equal(shareContentFor(task, reports, 'file'), null)
  task.report_package.artifact!.artifact_id = 'zip-bytes'
  task.report_package.reports[1].result_attempt = 2
  assert.equal(shareContentFor(task, reports, 'file'), null)
})
test('a report reference cannot name a file outside the frozen task', () => {
  const task = projection(), outside = structuredClone(task.files[0])
  assert.deepEqual(reportReference(task, outside), {task_id: 'task-1', round_id: 'round-1', file_version_id: 'file-v1', result_attempt: 1, report_id: 'report-1'})
  outside.file.id = 'outside-file-version'; outside.audit!.file_version_id = outside.file.id
  assert.equal(reportReference(task, outside), null)
})
test('ready download requires PDF metadata for that file, not just a ready label', () => {
  const task = projection(), row = task.files[0], report = row.audit!.report
  assert.equal(reportReady(task, row), false)
  report.generated_at = '2026-10-16T02:00:00Z'
  report.artifact = {artifact_id: 'pdf-1', filename: '控制计划.xlsx-审核报告.pdf', content_type: 'application/pdf', byte_size: 9}
  assert.equal(reportReady(task, row), true)
  report.artifact.filename = '不同文件-审核报告.pdf'
  assert.equal(reportReady(task, row), false)
})
test('long source filenames retain a bounded PDF suffix and stay downloadable', () => {
  const task = projection(), row = task.files[0], report = row.audit!.report
  row.file.original_filename = 'A'.repeat(250) + '.xlsx'
  report.generated_at = '2026-10-17T02:00:00Z'
  report.artifact = {artifact_id: 'pdf-1', filename: 'A'.repeat(246) + '-审核报告.pdf', content_type: 'application/pdf', byte_size: 9}
  assert.equal(reportReady(task, row), true)
  row.file.original_filename = '🧾'.repeat(247) + '.xlsx'
  report.artifact.filename = '🧾'.repeat(246) + '-审核报告.pdf'
  assert.equal(reportReady(task, row), true)
})
test('PDF reads reject HTML, mismatched sizes and wrong media types', async () => {
  const reference = {task_id: 'task-1', round_id: 'round-1', file_version_id: 'file-v1', result_attempt: 1, report_id: 'report-1'}
  const artifact = {artifact_id: 'pdf-1', filename: '控制计划.xlsx-审核报告.pdf', content_type: 'application/pdf' as const, byte_size: 9}
  const valid = new Blob(['%PDF-1.7\n'], {type: 'application/pdf'})
  assert.equal((await readReportPdf({readPdf: async () => valid}, reference, artifact)).size, 9)
  for (const blob of [new Blob(['<html>bad'], {type: 'application/pdf'}), new Blob(['%PDF-1.7\nextra'], {type: 'application/pdf'}), new Blob(['%PDF-1.7\n'], {type: 'text/html'})]) await assert.rejects(readReportPdf({readPdf: async () => blob}, reference, artifact), /invalid_pdf_artifact/)
})
test('ZIP downloads require an accepted ready artifact and matching bytes', async () => {
  const job: components['schemas']['AuditReportPackage'] = {package_id: 'zip-1', reports: [], receipt: {request_id: 'request-1', state: 'accepted', reason: null}, state: 'ready', artifact: {artifact_id: 'zip-bytes', filename: 'reports.zip', content_type: 'application/zip', byte_size: 7}, reason: null, retry_allowed: false}
  const valid = new Blob(['PKbytes'], {type: 'application/zip'})
  assert.equal((await readReportZip({readPackageFile: async () => valid}, job)).size, 7)
  for (const state of ['pending', 'rejected'] as const) await assert.rejects(readReportZip({readPackageFile: async () => {throw new Error('must_not_read')}}, {...job, receipt: {...job.receipt, state}}), /invalid_zip_artifact/)
  for (const blob of [new Blob(['<html/>'], {type: 'application/zip'}), new Blob(['PKextra-bytes'], {type: 'application/zip'}), new Blob(['PKbytes'], {type: 'text/html'})]) await assert.rejects(readReportZip({readPackageFile: async () => blob}, job), /invalid_zip_artifact/)
})
test('ZIP download and file sharing reject a ready package with a failed report preparation', async () => {
  const task = projection(), first = task.files[0]
  first.audit!.report.generated_at = '2026-10-16T02:00:00Z'
  first.audit!.report.artifact = {artifact_id: 'pdf-1', filename: '控制计划.xlsx-审核报告.pdf', content_type: 'application/pdf', byte_size: 9}
  const second = structuredClone(first)
  second.file.id = 'file-v2'; second.audit!.file_version_id = 'file-v2'; second.audit!.report.report_id = 'report-2'
  task.files.push(second)
  const reports = task.files.map(row => reportReference(task, row)!)
  const job: components['schemas']['AuditReportPackage'] = {package_id: 'zip-1', reports, receipt: {request_id: 'pack-1', state: 'accepted', reason: null}, state: 'ready', artifact: {artifact_id: 'zip-bytes', filename: '审核报告.zip', content_type: 'application/zip', byte_size: 7}, reason: null, retry_allowed: false, outcomes: [{reference: reports[0], state: 'prepared', reason: null}, {reference: reports[1], state: 'failed', reason: 'PDF 读取失败'}]}
  task.report_package = job
  assert.equal(shareContentFor(task, reports, 'file'), null)
  await assert.rejects(readReportZip({readPackageFile: async () => new Blob(['PKbytes'], {type: 'application/zip'})}, job), /invalid_zip_artifact/)
})
test('package outcomes identify each fixed report once and cannot invent a failed reason', () => {
  const reports = [{task_id: 'task-1', round_id: 'round-1', file_version_id: 'file-v1', result_attempt: 1, report_id: 'report-1'}, {task_id: 'task-2', round_id: 'round-2', file_version_id: 'file-v2', result_attempt: 2, report_id: 'report-2'}]
  const outcomes = reports.map(reference => ({reference, state: 'prepared' as const, reason: null}))
  const job: components['schemas']['AuditReportPackage'] = {package_id: 'zip-1', reports, receipt: {request_id: 'pack-1', state: 'accepted', reason: null}, state: 'failed', artifact: null, reason: '最终压缩失败，不是单报告失败', retry_allowed: false, outcomes: [...outcomes].reverse()}
  assert.equal(packageOutcomeError(job), '')
  const legacy = {...job}; delete legacy.outcomes
  assert.equal(packageOutcomeError(legacy), '')
  for (const broken of [[], [outcomes[0]], [outcomes[0], outcomes[0]], [...outcomes, outcomes[0]], [{...outcomes[0], state: 'failed' as const, reason: '  '}, outcomes[1]], [{...outcomes[0], state: 'failed' as const, reason: null}, outcomes[1]], [{...outcomes[0], state: 'unknown' as never}, outcomes[1]]]) assert.notEqual(packageOutcomeError({...job, outcomes: broken}), '')
  for (const field of ['task_id', 'round_id', 'file_version_id', 'result_attempt', 'report_id'] as const) {
    const reference = {...reports[0], [field]: field === 'result_attempt' ? 99 : 'other'}
    assert.notEqual(packageOutcomeError({...job, outcomes: [{...outcomes[0], reference}, outcomes[1]]}), '')
  }
  for (const state of ['pending', 'rejected'] as const) assert.notEqual(packageOutcomeError({...job, receipt: {...job.receipt, state}}), '')
  assert.notEqual(packageOutcomeError({...job, outcomes: null as never}), '')
  assert.notEqual(packageOutcomeError({...job, state: 'ready', outcomes: [{...outcomes[0], state: 'pending'}, outcomes[1]]}), '')
})
test('task ZIP scope requires distinct reports from its complete frozen result identity', () => {
  const task = projection(), second = structuredClone(task.files[0])
  second.file.id = 'file-v2'; second.audit!.file_version_id = 'file-v2'; second.audit!.report.report_id = 'report-2'
  task.files.push(second)
  const reports = task.files.map(row => reportReference(task, row)!)
  const job: components['schemas']['AuditReportPackage'] = {package_id: 'zip-1', reports: [...reports].reverse(), receipt: {request_id: 'pack-1', state: 'accepted', reason: null}, state: 'ready', artifact: {artifact_id: 'zip-bytes', filename: '审核报告.zip', content_type: 'application/zip', byte_size: 7}, reason: null, retry_allowed: false}
  task.report_package = job
  assert.equal(taskPackageScopeError(task), '')
  for (const field of ['task_id', 'round_id', 'file_version_id', 'result_attempt', 'report_id'] as const) {
    const other = {...reports[0], [field]: field === 'result_attempt' ? 99 : 'other'}
    assert.notEqual(taskPackageScopeError({...task, report_package: {...job, reports: [other, reports[1]]}}), '')
  }
  for (const invalid of [[], [reports[0]], [reports[0], reports[0]]]) assert.notEqual(taskPackageScopeError({...task, report_package: {...job, reports: invalid}}), '')
  assert.equal(taskPackageScopeError({...task, report_package: null}), '')
})
test('an explicit ZIP link reads only that request and rejects another request or frozen collection', async () => {
  const task = projection(), second = structuredClone(task.files[0])
  second.file.id = 'file-v2'; second.audit!.file_version_id = 'file-v2'; second.audit!.report.report_id = 'report-2'; task.files.push(second)
  const reports = task.files.map(row => reportReference(task, row)!)
  const job: components['schemas']['AuditReportPackage'] = {package_id: 'zip-1', reports, receipt: {request_id: 'pack-1', state: 'accepted', reason: null}, state: 'ready', artifact: {artifact_id: 'zip-bytes', filename: '审核报告.zip', content_type: 'application/zip', byte_size: 7}, reason: null, retry_allowed: false}
  const read = {readPackage: async (requestId: string) => {assert.equal(requestId, 'pack-1'); return job}}
  assert.equal(await readTaskReportPackage(read, task, 'pack-1'), job)
  await assert.rejects(readTaskReportPackage({readPackage: async () => ({...job, receipt: {...job.receipt, request_id: 'another-request'}})}, task, 'pack-1'), /package_link_mismatch/)
  await assert.rejects(readTaskReportPackage({readPackage: async () => ({...job, reports: [{...reports[0], task_id: 'other-task'}, reports[1]]})}, task, 'pack-1'), /package_link_mismatch/)
})
test('readback preserves the accepted round, file versions, rules and input selection', () => {
  const before = projection(), after = structuredClone(before)
  after.notification_enabled = false
  assert.equal(sameFrozenInputs(before, after), true)
  after.files[0].file.id = 'different-version'
  assert.equal(sameFrozenInputs(before, after), false)
  after.files[0].file.id = before.files[0].file.id
  after.files[0].match!.rule!.title = 'new-rule'
  assert.equal(sameFrozenInputs(before, after), false)
  after.files[0].match = structuredClone(before.files[0].match)
  after.files[0].selection = 'removed'
  assert.equal(sameFrozenInputs(before, after), false)
  after.files[0].selection = 'selected'; after.round_id = 'new-round'
  assert.equal(sameFrozenInputs(before, after), false)
})
test('the same frozen file cannot silently acquire another identified customer, part or document type', () => {
  const before = projection()
  for (const field of ['customer', 'model', 'part', 'part_number'] as const) {
    const after = structuredClone(before)
    after.files[0].match!.metadata[field] = 'different-identity'
    assert.equal(sameFrozenInputs(before, after), false, field)
  }
  const after = structuredClone(before)
  after.files[0].match!.document_type = 'PFMEA'
  assert.equal(sameFrozenInputs(before, after), false)
})
test('changed rule snapshots and item bodies cannot masquerade under the same IDs', () => {
  const task = projection(), audit = task.files[0].audit!
  audit.rule.title = '不同版本内容'
  assert.match(error(task), /审核结果与本次文件或规则不一致，请刷新/)
  audit.rule.title = '冻结规则'
  audit.checks[0].rule_item.rule_to_check = '改写的要求'
  assert.match(error(task), /审核项与本次规则不一致，请刷新/)
})
test('out-of-round evidence and contradictory counts invalidate trusted output', () => {
  const task = projection(), audit = task.files[0].audit!
  audit.checks[0].evidence[0].file_version_id = 'another-version'
  assert.match(error(task), /部分证据不在本次审核的文件中/)
  audit.checks[0].evidence[0].file_version_id = 'file-v1'
  audit.counts.ng_count = 1
  assert.match(error(task), /数量与明细不一致/)
})
test('unknown and failed checks cannot earn PASS or a formal report', () => {
  const task = projection(), audit = task.files[0].audit!
  audit.checks[0].finding!.status = 'UNABLE'; audit.checks[0].evidence_verified = false
  audit.counts = {pass_count: 0, ng_count: 0, unknown_count: 1, not_covered_count: 0, error_count: 0}
  assert.match(error(task), /结论为“符合”，但审核项结果不完整/)
  audit.conclusion = 'UNKNOWN'
  assert.match(error(task), /暂不生成正式报告/)
  audit.execution = 'review_failed'; audit.checks[0].execution = 'failed'
  audit.counts = {...audit.counts, unknown_count: 0, error_count: 1}
  assert.match(error(task), /暂不生成正式报告/)
})
test('empty rules and wrong progress fail closed', () => {
  const task = projection(), audit = task.files[0].audit!
  audit.progress.completed = 0
  assert.match(error(task), /进度与审核项状态不一致/)
  audit.checks = []; audit.progress = {completed: 0, total: 0}
  if (audit.rule.body.kind === 'engine') audit.rule.body.rules = []
  task.files[0].match!.rule = structuredClone(audit.rule)
  assert.match(error(task), /审核项与本次规则不一致，请刷新/)
})

test('the real API optional NA counter is checked without rejecting valid PASS or NG results', () => {
  const task = projection(), audit = task.files[0].audit!
  audit.counts.na_count = 0
  assert.equal(error(task), '')
  audit.counts.na_count = 1
  assert.match(error(task), /数量与明细不一致/)
  const check = structuredClone(audit.checks[0]); check.rule_item.id = '2'; check.finding!.item_key = '2'; check.finding!.status = 'NA'
  audit.checks.push(check)
  if (audit.rule.body.kind === 'engine' && task.files[0].match!.rule!.body.kind === 'engine') {
    audit.rule.body.rules.push(structuredClone(check.rule_item))
    task.files[0].match!.rule!.body.rules.push(structuredClone(check.rule_item))
  }
  audit.progress = {completed: 2, total: 2}
  assert.equal(error(task), '')
  audit.counts.na_count = 0
  assert.match(error(task), /数量与明细不一致/)
})

import type {components} from '../src/generated/openapi'
import type {AuditTask} from '../src/audit/intake'
import type {AuditEvidence} from '../src/audit/review-content'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../src/components/ui/table'
import {fixtureReport} from './audit-reports'
import reportSamples from './report-samples.json'

type Review = components['schemas']['AuditFileReview']
type Check = components['schemas']['AuditClauseEvaluation']

/** Explicit fixture projection. This module never calls a real audit or original API. */
export function fixtureAcceptance(task: AuditTask, requestId: string): AuditTask {
  const round = `preview-round-${requestId}`
  const files = structuredClone(task.files).map(file => {
    if (file.selection !== 'selected') return file
    if (file.match?.state !== 'matched' || !file.match.rule) return {...file, selection: 'removed' as const, removal_reason: '未匹配到规则，不进入本轮审核'}
    const rule = file.match.rule
    const checks: Check[] = rule.body.kind === 'engine' ? rule.body.rules.map(rule_item => ({rule_item, execution: 'queued', coverage: 'mapped', finding: null, evidence_verified: false, scope: null, scope_notice: null, evidence: []})) : []
    const audit: Review = {round_id: round, file_version_id: file.file.id, result_attempt: null, rule, execution: 'queued', progress: {completed: 0, total: checks.length}, failure: null, conclusion: null, counts: {pass_count: 0, ng_count: 0, unknown_count: 0, not_covered_count: 0, error_count: 0}, checks, summary: null, report: {state: 'not_eligible', report_id: null, reason: '审核尚未完成。'}}
    return {...file, audit}
  })
  return {...task, state: 'reviewing', round_id: round, acceptance: {request_id: requestId, state: 'accepted', reason: null}, progress: {completed: 0, total: files.filter(file => file.selection === 'selected').length}, notification_enabled: task.notification_enabled ?? true, notification_revision: task.notification_revision ?? 0, files}
}

/** A fixed intermediate read: unfinished checks have neither findings nor evidence. */
export function fixtureProgress(task: AuditTask): AuditTask {
  const completed = fixtureResults(task, 'populated')
  return {...task, files: task.files.map(file => {
    if (!file.audit || file.selection !== 'selected') return file
    const pfmea = file.match?.document_type === 'PFMEA', running = pfmea || file.match?.document_type === '控制计划'
    const checks = file.audit.checks.map((check, index) => pfmea && index === 0 ? completed.files.find(row => row.file.id === file.file.id)!.audit!.checks[0] : {...check, execution: running && (pfmea || index === 0) ? 'running' as const : 'queued' as const})
    return {...file, audit: {...file.audit, execution: running ? 'running' as const : 'queued' as const, result_attempt: running ? 1 : null, progress: {completed: pfmea ? 1 : 0, total: checks.length}, conclusion: pfmea ? 'NG' as const : null, counts: {...file.audit.counts, ng_count: pfmea ? 1 : 0}, checks}}
  })}
}

export function fixtureResults(task: AuditTask, scene: string): AuditTask {
  const selected = task.files.filter(file => file.selection === 'selected')
  const crossFile = selected.find(file => file.match?.document_type === '控制计划')
  const files = task.files.map(file => {
    if (!file.audit || file.selection !== 'selected') return file
    const failed = scene === 'reports-none' || file.match?.document_type === '过程流程图'
    const mixed = !failed && file.match?.document_type === 'PFMEA'
    const checks: Check[] = file.audit.checks.map((check, index) => {
      const status = failed ? 'ERR' : mixed ? index === 0 ? 'NG' : 'UNABLE' : 'PASS'
      const evidenceFile = mixed && index === 0 && crossFile ? crossFile : file
      const evidence: AuditEvidence = {file_version_id: evidenceFile.file.id, locator: mixed && index === 1 ? null : {kind: 'xlsx_cell', sheet: '审核示例', cell: index === 0 ? 'C12' : 'D18'}, excerpt: mixed ? index === 0 ? '控制计划与 PFMEA 的频次不同。' : '扫描内容不清晰，无法判断。' : '已核对指定要求。', location_gap: mixed && index === 1 ? '文字不清晰，无法定位原件。' : null, rendition: 'original', derived_file_version_id: null}
      return {...check, execution: failed ? 'failed' : 'completed', coverage: failed ? 'unsupported' : 'executed', finding: {item_key: check.rule_item.id, status, item: check.rule_item.category, classification: failed ? '文件读取失败。' : evidence.excerpt, suggestions: '', priority: null, locator: null, review: null}, evidence_verified: !failed && status !== 'UNABLE', scope: {total: 1, checked: status === 'UNABLE' || failed ? 0 : 1, unknown: status === 'UNABLE' || failed ? 1 : 0}, scope_notice: status === 'UNABLE' ? '检查范围未完整覆盖。' : null, evidence: failed ? [] : [evidence]}
    })
    const audit: Review = {...file.audit, result_attempt: 1, execution: failed ? 'review_failed' : 'completed', progress: {completed: checks.length, total: checks.length}, failure: failed ? {code: 'preview_reader_failure', message: '过程流程图读取失败。', retryable: false} : null, conclusion: failed ? 'UNKNOWN' : mixed ? 'NG' : 'PASS', counts: {pass_count: mixed || failed ? 0 : checks.length, ng_count: mixed ? 1 : 0, unknown_count: mixed ? 1 : 0, not_covered_count: 0, error_count: failed ? checks.length : 0}, checks, summary: failed ? null : {conclusion: mixed ? 'NG' : 'PASS', text: mixed ? '1 项不符合，1 项无法判断。' : '2 项要求均已完成验证。', corrective_actions: mixed ? ['核对并统一控制计划与 PFMEA 的频次；提供清晰原件。'] : [], extended_suggestions: []}, report: failed || mixed ? {state: 'not_eligible', report_id: null, reason: failed ? '审核异常，无法生成报告。' : '仍有无法判断项，暂不能生成报告。'} : {state: 'ready', report_id: `preview-report-${file.file.id}`, reason: null}}
    if (scene === 'counts-conflict' && mixed) audit.counts = {pass_count: 2, ng_count: 0, unknown_count: 0, not_covered_count: 0, error_count: 0}
    if (scene === 'summary-conflict' && mixed && audit.summary) audit.summary.conclusion = 'PASS'
    if (scene === 'scope-conflict' && mixed) {
      audit.checks[1].finding!.status = 'PASS'; audit.checks[1].evidence_verified = true
      audit.counts = {...audit.counts, pass_count: 1, unknown_count: 0}
      audit.summary!.text = '1 项不符合，1 项满足；检查范围记录不完整。'
      audit.report = {state: 'ready', report_id: `preview-report-${file.file.id}`, reason: null}
    }
    if (scene === 'evidence-outside' && mixed) audit.checks[0].evidence[0].file_version_id = 'preview-not-in-this-round'
    if (scene === 'evidence-empty' && mixed) audit.checks[0].evidence[0].excerpt = ''
    if (scene === 'rule-conflict' && mixed) audit.checks[0].rule_item = {...audit.checks[0].rule_item, rule_to_check: '被错误改写的要求'}
    if (scene === 'failed-report-ready' && failed) audit.report = {state: 'ready', report_id: 'invalid-preview-report', reason: null}
    if (!failed && (!mixed || scene.startsWith('reports-'))) {
      const facts = reportSamples.documents[mixed ? 0 : 1]
      audit.checks = audit.checks.map((check, index) => {
        const fact = facts.checks[index], evidenceFile = index === 0 && crossFile ? crossFile : file
        return {...check, finding: {...check.finding!, status: fact.status === 'NG' ? 'NG' : 'PASS', classification: fact.observation}, evidence_verified: true, scope: {checked: 1, total: 1, unknown: 0}, scope_notice: null, evidence: [{...check.evidence[0], file_version_id: evidenceFile.file.id, locator: {kind: 'xlsx_cell', sheet: '审核示例', cell: fact.cell}, excerpt: fact.observation, location_gap: null}]}
      })
      audit.counts = {...audit.counts, pass_count: facts.checks.filter(check => check.status === 'PASS').length, ng_count: facts.checks.filter(check => check.status === 'NG').length, unknown_count: 0}
      audit.summary = {conclusion: mixed ? 'NG' : 'PASS', text: facts.summary, corrective_actions: facts.corrective_actions, extended_suggestions: []}
      audit.report = {...fixtureReport(file.file.original_filename), report_id: `preview-report-${file.file.id}`}
    }
    if (['reports-failed', 'reports-unknown', 'reports-policy', 'reports-generate-race'].includes(scene) && !mixed && !failed) audit.report = {...audit.report, state: 'failed', artifact: null, generated_at: null, reason: 'PDF 生成失败。', regenerate_allowed: scene !== 'reports-policy', regenerate_reason: scene === 'reports-policy' ? '暂不能重新生成报告。' : null}
    if (scene === 'reports-generating' && !mixed && !failed) audit.report = {...audit.report, state: 'generating', artifact: null, generated_at: null, reason: '正在生成 PDF。'}
    return {...file, audit}
  })
  if (scene === 'audit-input-conflict') {
    const row = files.find(file => file.audit)!
    row.file = {...row.file, id: 'preview-replaced-version', original_filename: '已被换成最新版本的文件.xlsx'}
    row.audit = {...row.audit!, file_version_id: row.file.id}
  }
  const pending = files.some(file => file.audit && ['pending', 'generating'].includes(file.audit.report.state))
  return {...task, state: pending ? 'reviewing' : 'completed', completion_reason: pending ? null : 'processed', progress: {completed: selected.length, total: selected.length}, files}
}

export function FixtureOriginal({file, evidence}: {file: components['schemas']['FileVersion']; evidence: AuditEvidence | null}) {
  return <div className="space-y-4"><Table><TableHeader><TableRow><TableHead>文件</TableHead><TableHead>原件摘录</TableHead></TableRow></TableHeader><TableBody><TableRow><TableCell className="whitespace-normal">{file.original_filename}</TableCell><TableCell className="whitespace-normal">{evidence?.excerpt || '请选择判定依据。'}</TableCell></TableRow></TableBody></Table></div>
}

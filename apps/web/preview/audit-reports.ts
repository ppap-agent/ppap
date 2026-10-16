import type {components} from '../src/generated/openapi'
import type {AuditReportAdapter} from '../src/audit/report-contract'
import {sameSnapshot} from '../src/audit/result-projection'
import {zipSync, strToU8} from 'fflate'
import controlPlan from './reports/control-plan.pdf?url'
import pfmea from './reports/pfmea.pdf?url'
import samples from './report-samples.json'

const artifacts = {
  'preview-pdf-control-plan': {url: controlPlan},
  'preview-pdf-pfmea': {url: pfmea},
}
const bytes = new Map<string, Promise<Blob>>()
function readArtifact(id: string): Promise<Blob> {
  const sample = artifacts[id as keyof typeof artifacts]
  if (!sample) return Promise.reject(new Error('unknown_preview_artifact'))
  if (!bytes.has(id)) bytes.set(id, fetch(sample.url).then(response => {if (!response.ok) throw new Error('preview_artifact_unavailable'); return response.blob()}).catch(error => {bytes.delete(id); throw error}))
  return bytes.get(id)!
}
export async function withReportBytes(task: components['schemas']['AuditTask']): Promise<components['schemas']['AuditTask']> {
  const files = await Promise.all(task.files.map(async file => {
    const artifact = file.audit?.report.artifact
    if (!artifact) return file
    const blob = await readArtifact(artifact.artifact_id)
    return {...file, audit: {...file.audit!, report: {...file.audit!.report, artifact: {...artifact, byte_size: blob.size}}}}
  }))
  return {...task, files}
}

/** Fixed PDFs repeat only the explicit sample facts, never arbitrary uploaded data. */
export function fixtureReport(name: string): components['schemas']['AuditFileReport'] {
  if (!['PFMEA.xlsx', '控制计划.xlsx'].includes(name)) return {state: 'failed', report_id: '', reason: '此文件的 PDF 暂不可用。', regenerate_allowed: false, regenerate_reason: '暂不能重新生成报告。'}
  const document = samples.documents.find(row => row.name === name)!
  return {state: 'ready', report_id: '', reason: null, generated_at: samples.generated_at, artifact: {artifact_id: `preview-pdf-${document.asset}`, filename: `${name}-审核报告.pdf`, content_type: 'application/pdf', byte_size: 0}, regenerate_allowed: false, regenerate_reason: '暂不能重新生成报告。', generation_request: null}
}
export function fixtureReports(tasks: Map<string, components['schemas']['AuditTask']>, scene: string): AuditReportAdapter {
  const operations = new Map<string, components['schemas']['AuditReportUpdate']>()
  const packages = new Map<string, {job: components['schemas']['AuditReportPackage']; sources: {artifact: components['schemas']['AuditReportArtifact']; rule: string; generatedAt: string | null | undefined}[]; blob?: Blob}>()
  const failedPackages = new Set<string>()
  const reads = new Set<string>()
  const invalidPackageReads = new Set<string>()
  function packageObservation(job: components['schemas']['AuditReportPackage']) {
    const next = structuredClone(job)
    if (job.state !== 'ready' || !scene.startsWith('reports-zip-outcome-') || invalidPackageReads.has(job.receipt.request_id)) return next
    invalidPackageReads.add(job.receipt.request_id)
    const outcomes = next.outcomes!
    if (scene === 'reports-zip-outcome-missing') next.outcomes = outcomes.slice(0, 1)
    if (scene === 'reports-zip-outcome-duplicate') next.outcomes = [outcomes[0], outcomes[0]]
    if (scene === 'reports-zip-outcome-reference') outcomes[0].reference.report_id = 'preview-other-report'
    if (scene === 'reports-zip-outcome-blank') outcomes[0] = {...outcomes[0], state: 'failed', reason: '  '}
    if (scene === 'reports-zip-outcome-ready') {outcomes[0] = {...outcomes[0], state: 'failed', reason: '报告未准备完成，无法下载 ZIP。'}; savePackage(next)}
    return next
  }
  function target(reference: components['schemas']['AuditReportReference']) {
    const task = tasks.get(reference.task_id), row = task?.files.find(file => file.file.id === reference.file_version_id)
    if (!task || task.round_id !== reference.round_id || row?.audit?.result_attempt !== reference.result_attempt || row.audit.report.report_id !== reference.report_id) throw new Error('unknown_preview_report_reference')
    return {task, row, audit: row.audit}
  }
  function savePackage(job: components['schemas']['AuditReportPackage']) {
    if (!job.reports.every(reference => reference.task_id === job.reports[0].task_id)) return
    const task = tasks.get(job.reports[0].task_id)!
    tasks.set(task.id, {...task, report_package: structuredClone(job)})
  }
  return {
  readPdf: async (reference, artifact) => {
    const {audit} = target(reference)
    if (!sameSnapshot(audit.report.artifact, artifact)) throw new Error('wrong_preview_artifact')
    return readArtifact(artifact.artifact_id)
  },
  regenerate: async (reference, requestId) => {
    const {task, row, audit} = target(reference)
    if (!audit.report.regenerate_allowed) return {reference, receipt: {request_id: requestId, state: 'rejected', reason: audit.report.regenerate_reason || '暂不能重新生成报告。'}, report: null}
    const receipt = {request_id: requestId, state: 'accepted' as const, reason: null}
    const report = {...audit.report, state: 'generating' as const, artifact: null, generated_at: null, regenerate_allowed: false, reason: '正在重新生成 PDF。', generation_request: receipt}
    const update = {reference: structuredClone(reference), receipt, report}
    operations.set(requestId, update)
    tasks.set(task.id, {...task, files: task.files.map(file => file.file.id === row.file.id ? {...file, audit: {...audit, report}} : file)})
    if (scene === 'reports-unknown') throw new Error('preview_response_lost')
    return structuredClone(update)
  },
  readRegeneration: async (reference, requestId) => {
    const update = operations.get(requestId)
    if (!update || !sameSnapshot(reference, update.reference)) throw new Error('unknown_preview_report_request')
    if (scene === 'reports-unknown' && !reads.has(requestId)) {reads.add(requestId); return {reference, receipt: {request_id: requestId, state: 'pending', reason: '受理结果待确认。'}, report: null}}
    return {...structuredClone(update), report: structuredClone(target(reference).audit.report)}
  },
  createPackage: async (reports, requestId) => {
    if (reports.length < 2 || reports.some(reference => target(reference).audit.report.state !== 'ready')) throw new Error('unready_preview_report')
    const job: components['schemas']['AuditReportPackage'] = {package_id: `preview-zip-${requestId}`, reports: structuredClone(reports), outcomes: reports.map(reference => ({reference: structuredClone(reference), state: 'pending', reason: null})), receipt: {request_id: requestId, state: 'accepted', reason: null}, state: 'generating', artifact: null, reason: '正在打包报告。', retry_allowed: false}
    if (scene === 'reports-zip-legacy') delete job.outcomes
    const sources = reports.map(reference => {const {audit} = target(reference); return {artifact: structuredClone(audit.report.artifact!), rule: audit.rule.id, generatedAt: audit.report.generated_at}})
    packages.set(requestId, {job, sources})
    savePackage(job)
    if (scene === 'reports-zip-unknown') throw new Error('preview_zip_response_lost')
    return structuredClone(job)
  },
  readPackage: async requestId => {
    const stored = packages.get(requestId)
    if (!stored) throw new Error('unknown_preview_package')
    if (scene === 'reports-zip-unknown' && !reads.has(requestId)) {reads.add(requestId); return {...structuredClone(stored.job), receipt: {request_id: requestId, state: 'pending', reason: '受理结果待确认。'}, state: 'pending'}}
    if (stored.blob) return packageObservation(stored.job)
    if (stored.job.state === 'failed') return structuredClone(stored.job)
    const collection = JSON.stringify(stored.job.reports)
    if (['reports-zip-failed', 'reports-zip-write-failed', 'reports-zip-pending-failed', 'reports-zip-no-retry'].includes(scene) && !failedPackages.has(collection)) {
      failedPackages.add(collection)
      const preparation = scene === 'reports-zip-write-failed' ? 'prepared' : scene === 'reports-zip-pending-failed' ? 'pending' : 'failed'
      stored.job = {...stored.job, state: 'failed', outcomes: stored.job.reports.map<components['schemas']['AuditReportPackageOutcome']>((reference, index) => ({reference: structuredClone(reference), state: index === 1 ? preparation : 'prepared', reason: index === 1 && preparation === 'failed' ? '控制计划 PDF 暂时无法读取。' : null})).reverse(), reason: scene === 'reports-zip-write-failed' ? 'ZIP 保存失败，各份 PDF 已准备完成。' : scene === 'reports-zip-no-retry' ? 'ZIP 打包失败，暂不能再次打包。' : 'ZIP 打包失败。', retry_allowed: scene !== 'reports-zip-no-retry'}
      savePackage(stored.job); return structuredClone(stored.job)
    }
    const entries: Record<string, Uint8Array> = {}
    const index = ['预览报告', '报告版本以本清单为准。', '']
    for (const [position, reference] of stored.job.reports.entries()) {
      const {artifact, rule, generatedAt} = stored.sources[position]
      const blob = await readArtifact(artifact.artifact_id)
      const base = artifact.filename.replace(/[/\\]/g, '_')
      let name = base, count = 2
      while (entries[name]) name = `${base.slice(0, -4)} (${count++}).pdf`
      entries[name] = new Uint8Array(await blob.arrayBuffer())
      index.push(name, `冻结文件版本：${reference.file_version_id}`, `审核轮次：${reference.round_id}`, `执行尝试：${reference.result_attempt}`, `冻结规则版本：${rule}`, `PDF 生成时间：${generatedAt}`, '')
    }
    entries['报告清单.txt'] = strToU8(index.join('\n'))
    const bytes = zipSync(entries)
    stored.blob = new Blob([new Uint8Array(bytes)], {type: 'application/zip'})
    stored.job = {...stored.job, state: 'ready', outcomes: stored.job.reports.map(reference => ({reference: structuredClone(reference), state: 'prepared', reason: null})), reason: null, artifact: {artifact_id: `preview-zip-bytes-${requestId}`, filename: '审核报告-示例.zip', content_type: 'application/zip', byte_size: stored.blob.size}}
    if (scene === 'reports-zip-legacy') delete stored.job.outcomes
    savePackage(stored.job)
    return packageObservation(stored.job)
  },
  readPackageFile: async job => {
    const stored = packages.get(job.receipt.request_id)
    if (!stored?.blob || !sameSnapshot(stored.job, job)) throw new Error('wrong_preview_package')
    return stored.blob
  },
  }
}

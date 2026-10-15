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
  if (!['PFMEA.xlsx', '控制计划.xlsx'].includes(name)) return {state: 'failed', report_id: '', reason: '此文件名没有对应的示例 PDF；不会用另一文件的报告冒充。', regenerate_allowed: false, regenerate_reason: '没有该文件的固定 PDF 样例。'}
  const document = samples.documents.find(row => row.name === name)!
  return {state: 'ready', report_id: '', reason: '交互预览：PDF 为明确标识的示例，不是真实审核结果。', generated_at: samples.generated_at, artifact: {artifact_id: `preview-pdf-${document.asset}`, filename: `${name}-审核报告.pdf`, content_type: 'application/pdf', byte_size: 0}, regenerate_allowed: false, regenerate_reason: '重生成能力尚未接入，收费政策未定。', generation_request: null}
}
export function fixtureReports(tasks: Map<string, components['schemas']['AuditTask']>, scene: string): AuditReportAdapter {
  const operations = new Map<string, components['schemas']['AuditReportUpdate']>()
  const packages = new Map<string, {job: components['schemas']['AuditReportPackage']; sources: {artifact: components['schemas']['AuditReportArtifact']; rule: string; generatedAt: string | null | undefined}[]; blob?: Blob}>()
  const failedPackages = new Set<string>()
  const reads = new Set<string>()
  function target(reference: components['schemas']['AuditReportReference']) {
    const task = tasks.get(reference.task_id), row = task?.files.find(file => file.file.id === reference.file_version_id)
    if (!task || task.round_id !== reference.round_id || row?.audit?.result_attempt !== reference.result_attempt || row.audit.report.report_id !== reference.report_id) throw new Error('unknown_preview_report_reference')
    return {task, row, audit: row.audit}
  }
  function savePackage(job: components['schemas']['AuditReportPackage']) {
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
    if (!audit.report.regenerate_allowed) return {reference, receipt: {request_id: requestId, state: 'rejected', reason: audit.report.regenerate_reason || '示例服务未允许重生成。'}, report: null}
    const receipt = {request_id: requestId, state: 'accepted' as const, reason: null}
    const report = {...audit.report, state: 'generating' as const, artifact: null, generated_at: null, regenerate_allowed: false, reason: '示例：正在重生成相同结果的 PDF，不重跑审核。', generation_request: receipt}
    const update = {reference: structuredClone(reference), receipt, report}
    operations.set(requestId, update)
    tasks.set(task.id, {...task, files: task.files.map(file => file.file.id === row.file.id ? {...file, audit: {...audit, report}} : file)})
    if (scene === 'reports-unknown') throw new Error('preview_response_lost')
    return structuredClone(update)
  },
  readRegeneration: async (reference, requestId) => {
    const update = operations.get(requestId)
    if (!update || !sameSnapshot(reference, update.reference)) throw new Error('unknown_preview_report_request')
    if (scene === 'reports-unknown' && !reads.has(requestId)) {reads.add(requestId); return {reference, receipt: {request_id: requestId, state: 'pending', reason: '示例：尚未取得受理回执。'}, report: null}}
    return {...structuredClone(update), report: structuredClone(target(reference).audit.report)}
  },
  createPackage: async (reports, requestId) => {
    if (reports.length < 2 || reports.some(reference => target(reference).audit.report.state !== 'ready')) throw new Error('unready_preview_report')
    const job: components['schemas']['AuditReportPackage'] = {package_id: `preview-zip-${requestId}`, reports: structuredClone(reports), receipt: {request_id: requestId, state: 'accepted', reason: null}, state: 'generating', artifact: null, reason: '示例：打包工作已受理，单份 PDF 下载仍可使用。', retry_allowed: false}
    const sources = reports.map(reference => {const {audit} = target(reference); return {artifact: structuredClone(audit.report.artifact!), rule: audit.rule.id, generatedAt: audit.report.generated_at}})
    packages.set(requestId, {job, sources})
    savePackage(job)
    if (scene === 'reports-zip-unknown') throw new Error('preview_zip_response_lost')
    return structuredClone(job)
  },
  readPackage: async requestId => {
    const stored = packages.get(requestId)
    if (!stored) throw new Error('unknown_preview_package')
    if (scene === 'reports-zip-unknown' && !reads.has(requestId)) {reads.add(requestId); return {...structuredClone(stored.job), receipt: {request_id: requestId, state: 'pending', reason: '受理结果尚未知。'}, state: 'pending'}}
    if (stored.blob) return structuredClone(stored.job)
    const collection = JSON.stringify(stored.job.reports)
    if (scene === 'reports-zip-failed' && !failedPackages.has(collection)) {
      failedPackages.add(collection)
      stored.job = {...stored.job, state: 'failed', reason: '示例：打包已明确失败，已有 PDF 完整保留。', retry_allowed: true}
      savePackage(stored.job); return structuredClone(stored.job)
    }
    const entries: Record<string, Uint8Array> = {}
    const index = ['交互预览 - 示例报告，不是真实审核结果。', '以下文件名对应创建 ZIP 时固定的审核结果，不表示最新版本。', '']
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
    stored.job = {...stored.job, state: 'ready', reason: null, artifact: {artifact_id: `preview-zip-bytes-${requestId}`, filename: '审核报告-示例.zip', content_type: 'application/zip', byte_size: stored.blob.size}}
    savePackage(stored.job)
    return structuredClone(stored.job)
  },
  readPackageFile: async job => {
    const stored = packages.get(job.receipt.request_id)
    if (!stored?.blob || !sameSnapshot(stored.job, job)) throw new Error('wrong_preview_package')
    return stored.blob
  },
  }
}

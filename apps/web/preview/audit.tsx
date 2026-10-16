import {createRoot} from 'react-dom/client'
import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import '../src/index.css'
import {AuditIntake, type AuditIntakeAdapter, type AuditTask, type AuditTaskFile} from '../src/audit/intake'
import type {components} from '../src/generated/openapi'
import {Button} from '../src/components/ui/button'
import {fixtureAcceptance, fixtureProgress, fixtureResults, FixtureOriginal} from './audit-results'
import {fixtureReports, fixtureReport, withReportBytes} from './audit-reports'
import reportSamples from './report-samples.json'
import {fixtureSharing} from './audit-sharing'
import {fixtureArchive} from './audit-archive'
import {fixtureTemplates} from './audit-templates'
import {fixtureFeedback} from './audit-feedback'
import {fixtureTemplateSharing} from './audit-template-sharing'
import {fixtureTemplatePermissions} from './audit-template-permissions'
import {fixtureUsage} from './audit-usage'
import {FixtureRemoteOriginal, fixtureOriginalFile} from './audit-original'
import type {RemoteOriginalSource} from '../src/journey/remote-source'
import {fixtureSupport} from './audit-support'
import {displayReadFixture, fixtureDisplay} from './audit-display'
import {fileDisplayValues} from '../src/audit/display-info-contract'
import {usageNotices} from '../src/audit/usage-contract'
import {reportReference} from '../src/audit/result-projection'

// Explicit UI fixtures only. No production entry imports this adapter.
const query = new URLSearchParams(location.search)
const scene = query.get('scene') || 'populated'
const automaticObservation = scene.startsWith('restore-observe') || scene === 'observe-matching'
const delayedDisplay = scene === 'restore-display-loading' || scene === 'restore-display-denied'
const templateAdapter = fixtureTemplates(scene)
const feedbackAdapter = fixtureFeedback(scene)
const supportAdapter = fixtureSupport(scene)
document.documentElement.classList.toggle('dark', query.get('theme') === 'dark')
const documents = [
  ...reportSamples.documents,
  {type: '过程流程图', name: '过程流程图.pdf', rule: '示例过程流程图规则 · v3', mime: 'application/pdf'},
]
const driveCatalog = [
  ...documents.map((document, index) => ({source: {token: `preview-document-${index}`, name: document.name, type: 'file'}, index, filename: document.name, mime: document.mime})),
  {source: {token: 'preview-native-sheet', name: '控制计划（飞书表格）', type: 'sheet'}, index: 1, filename: '控制计划（飞书表格）.xlsx', mime: documents[1].mime},
  {source: {token: 'preview-native-bitable', name: '控制计划（多维表格）', type: 'bitable'}, index: 1, filename: '控制计划（多维表格）.xlsx', mime: documents[1].mime},
  {source: {token: 'preview-native-docx', name: '控制计划（飞书文档）', type: 'docx'}, index: 1, filename: '控制计划（飞书文档）.docx', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
  {source: {token: 'preview-native-doc', name: '控制计划（旧版文档）', type: 'doc'}, index: 1, filename: '控制计划（旧版文档）.docx', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
]
let nextTask = 1
let driveReads = 0
const accepted = new Map<string, ReturnType<typeof fixtureAcceptance>>()
const confirmationInputs = new Map<string, AuditTask>()
const taskStore = new Map<string, AuditTask>()
const displayAdapter = fixtureDisplay(taskStore, scene)
const reportAdapter = fixtureReports(taskStore, scene)
const sharingAdapter = fixtureSharing(taskStore, scene)
const templateSharingAdapter = fixtureTemplateSharing(templateAdapter, sharingAdapter, scene)
const templatePermissionAdapter = fixtureTemplatePermissions(scene)
let recoveryRead = 0
const foreignPackageReads = new Set<string>()
let authenticated = false
const acceptanceReads = new Map<string, number>()
const uploadReceipts = new Map<string, components['schemas']['AuditUploadItem']>()
const transferReads = new Map<string, number>()
const transferAttempts = new WeakSet<File>()
const recognitionReceipts = new Map<string, AuditTask>()
const recognitionReads = new Map<string, number>()
let recognitionCommands = 0
function sample(index: number): AuditTaskFile {
  const id = `preview-file-${crypto.randomUUID()}`
  const document = documents[index]
  const file: components['schemas']['FileVersion'] = {file_id: `preview-document-${index}`, id, original_filename: document.name, content_type: document.mime, byte_size: 240000 + index * 80000, status: 'ready', sha256: null}
  return {file: scene.startsWith('original-') ? fixtureOriginalFile(file) : file, selection: 'selected', removal_reason: null, validation_id: `preview-validation-${id}`, validation: {state: 'valid', reasons: []}, match: null}
}
const adapter: AuditIntakeAdapter = {
  authenticate: async () => {authenticated = true},
  drive: {
    list: async () => {
      driveReads += 1
      if (scene === 'drive-denied') throw new Error('authorization_required')
      if (scene === 'drive-error' && driveReads === 1) throw new Error('preview_read_failed')
      return {files: scene === 'drive-empty' ? [] : [...driveCatalog.map(row => row.source), {token: 'preview-image', name: '现场照片.jpg', type: 'file'}]}
    },
  },
  samples: () => {
    const files = documents.map((_, index) => sample(index))
    if (scene === 'recognition-provenance-union') return [files[0]]
    if (scene === 'checking' || scene === 'checking-loading') {
      files[1].validation = {state: 'checking', reasons: ['示例：等待预检查结果']}
      files[2].validation = {state: 'invalid', reasons: ['示例：文件加密，不能读取']}
    }
    if (scene === 'invalid') files.push({...sample(0), file: {...sample(0).file, original_filename: '加密文件.xlsx'}, validation: {state: 'invalid', reasons: ['文件加密或异常，无法识别']}})
    if (scene === 'reports-duplicate') {const duplicate = sample(1); files.push({...duplicate, file: {...duplicate.file, file_id: 'preview-duplicate-document'}})}
    return files
  },
  readValidation: async row => ({validation: driveCatalog.some(fixture => fixture.source.token === row.file.file_id) ? {state: 'valid', reasons: []} : row.validation, validation_id: `preview-validation-${row.file.id}`}),
  confirm: async (task, requestId) => {
    confirmationInputs.set(task.id, structuredClone(task))
    const result = accepted.get(task.id) || fixtureAcceptance(task, requestId)
    if (scene === 'confirm-rule-conflict') {
      const rule = result.files[0].match?.rule
      if (rule?.body.kind === 'engine') rule.body.rules[0].rule_to_check = '同一规则 ID 的错误改写'
    }
    if (scene === 'confirm-metadata-conflict' && result.files[0].match) result.files[0].match.metadata.customer = '被静默替换的客户'
    accepted.set(task.id, result)
    taskStore.set(task.id, result)
    if (scene === 'confirm-unknown') throw new Error('preview_response_lost')
    return result
  },
  readTask: async (id, requestId) => {
    recoveryRead += 1
    if (scene === 'restore-auth' && !authenticated) throw Object.assign(new Error('preview_auth_required'), {status: 401})
    if (scene === 'restore-denied') throw Object.assign(new Error('preview_forbidden'), {status: 403})
    if (scene === 'restore-error' && recoveryRead === 1) throw new Error('preview_offline')
    if (scene === 'restore-display-read-failed' && recoveryRead === 2) throw new Error('preview_display_offline')
    const task = taskStore.get(id)
    if (!task) throw Object.assign(new Error('preview_not_found'), {status: 404})
    if (task.state === 'completed' && task.completion_reason === 'no_matching_rules') return structuredClone(task)
    if (scene === 'reports-share-external' && recoveryRead === 2) {
      const reference = reportReference(task, task.files[0])
      if (!reference) throw new Error('preview_external_report_missing')
      try {await sharingAdapter.send({request_id: 'preview-external-share', organization_id: 'preview-org', reports: [reference], targets: [{kind: 'user', id: 'preview-member-li'}], content: {kind: 'link', artifact: null, package_id: null}})}
      catch (error) {if (!(error instanceof Error) || error.message !== 'preview_share_receipt_lost') throw error}
      return structuredClone(taskStore.get(id)!)
    }
    if (scene === 'restore-observe-regression' && recoveryRead === 2) return fixtureAcceptance(task, task.acceptance!.request_id)
    if (scene === 'restore-observe-missing-progress' && recoveryRead === 2) {const next = structuredClone(task); delete next.progress; return next}
    if (scene.startsWith('restore-') && recoveryRead === (scene === 'restore-error' || scene === 'restore-auth' ? 2 : 1) || scene.startsWith('reports-') && id === 'preview-report-task' && recoveryRead === 1) {const next = await withReportBytes(task); taskStore.set(id, next); return structuredClone(next)}
    if ((scene.startsWith('reports-') || scene === 'restore-report-conflict') && task.round_id && task.files.every(file => file.selection !== 'selected' || ['completed', 'review_failed'].includes(file.audit?.execution || ''))) {
      const files = task.files.map(file => file.audit?.report.state === 'generating' ? {...file, audit: {...file.audit, report: {...fixtureReport(file.file.original_filename), report_id: file.audit.report.report_id, generation_request: file.audit.report.generation_request}}} : file)
      const result = await withReportBytes({...task, state: 'completed', completion_reason: 'processed', files})
      taskStore.set(id, result); return structuredClone(result)
    }
    if (task.round_id && task.files.every(file => file.selection !== 'selected' || ['completed', 'review_failed'].includes(file.audit?.execution || ''))) return structuredClone(task)
    const result = accepted.get(id)
    if (result) {
      const reads = (acceptanceReads.get(id) || 0) + 1
      acceptanceReads.set(id, reads)
      if (['confirm-unknown', 'restore-unknown'].includes(scene) && reads === 1) return {...(confirmationInputs.get(id) || task), acceptance: {request_id: requestId || task.acceptance!.request_id, state: 'pending', reason: null}}
      if (requestId) return result
      const next = await withReportBytes(fixtureResults(result, scene)); taskStore.set(id, next); return next
    }
    if (task.round_id) {const next = await withReportBytes(fixtureResults(task, scene)); taskStore.set(id, next); return next}
    const next = fixtureMatch(task.files, task.analysis_mode, task.customer_preference, task, task.recognition_request_id || task.id)
    taskStore.set(id, next)
    return next
  },
  upload: async (input, requestId) => {
    let receipt: components['schemas']['AuditUploadItem']
    if (input.kind === 'feishu') {
      const fixture = driveCatalog.find(row => row.source.token === input.file.token)
      if (!fixture) throw new Error('unknown_preview_source')
      const file = sample(fixture.index).file
      receipt = {request_id: requestId, state: 'ready', file: {...file, file_id: fixture.source.token, original_filename: fixture.filename, content_type: fixture.mime}, reason: null, retry_allowed: false}
    } else {
      const file = input.file
      const reason = file.size > 200 * 1024 * 1024 ? '文件超过 200 MB，未开始上传。' : !/\.(xlsx|xls|xlsm|doc|docx|pdf)$/i.test(file.name) ? '非审核支持格式，传输未开始。' : ''
      const knownFailure = scene === 'upload-partial' && file.name === '明确失败.xlsx' && !transferAttempts.has(file)
      transferAttempts.add(file)
      receipt = reason || knownFailure ? {request_id: requestId, state: 'failed', file: null, reason: reason || '示例：服务已明确终结请求，没有创建文件，也没有在途写入。可安全重试此文件。', retry_allowed: knownFailure && !reason} : {request_id: requestId, state: 'ready', file: {file_id: `preview-local-${crypto.randomUUID()}`, id: `preview-local-version-${crypto.randomUUID()}`, original_filename: file.name, content_type: file.type, byte_size: file.size, status: 'local_preview', sha256: null}, reason: null, retry_allowed: false}
    }
    uploadReceipts.set(requestId, receipt)
    if (scene === 'upload-partial' && input.file.name === '结果未知.docx') throw new Error('preview_upload_response_lost')
    return receipt
  },
  readUpload: async requestId => {
    const receipt = uploadReceipts.get(requestId)
    if (!receipt) throw new Error('unknown_preview_request')
    const reads = (transferReads.get(requestId) || 0) + 1
    transferReads.set(requestId, reads)
    return scene === 'upload-partial' && reads === 1 ? {...receipt, state: 'unknown', file: null, retry_allowed: false, reason: '示例：仍在处理中，不能确认未接收。'} : receipt
  },
  match: async (files, mode, customer, previous, requestId) => {
    recognitionCommands += 1
    if (scene === 'recognition-conflict' && recognitionCommands === 1) throw Object.assign(new Error('preview_input_revision_conflict'), {status: 409})
    if (scene === 'recognition-provenance-union' && previous) {
      if (recognitionCommands === 2) {
        const excluded = sample(0)
        taskStore.set(previous.id, {...previous, input_revision: previous.input_revision! + 1, recognition_request_id: 'preview-other-recognition', files: [...previous.files, sample(2), {...excluded, file: {...excluded.file, file_id: 'preview-removed-source', original_filename: '已排除来源.xlsx'}, selection: 'removed', removal_reason: '其他页面已排除'}]})
        throw Object.assign(new Error('preview_input_revision_conflict'), {status: 409})
      }
      const current = taskStore.get(previous.id)!
      if (current.input_revision !== previous.input_revision || current.files.some(row => !files.some(file => file.file.id === row.file.id))) throw Object.assign(new Error('preview_provenance_missing'), {status: 409})
    }
    const task = {...fixtureMatch(files, mode, customer, previous, requestId, previous?.number || `PPAP-示例-${String(nextTask++).padStart(3, '0')}`), input_revision: previous ? (previous.input_revision ?? 0) + 1 : 0}
    taskStore.set(task.id, task); recognitionReceipts.set(requestId, structuredClone(task))
    if (scene === 'recognition-superseded' && recognitionCommands === 1) {
      taskStore.set(task.id, {...task, input_revision: task.input_revision + 1, recognition_request_id: 'preview-other-recognition', files: task.files.map((row, index) => index === 0 ? {...row, selection: 'unselected'} : row)})
      throw new Error('preview_recognition_reply_lost')
    }
    if (scene === 'recognition-unknown') throw new Error('preview_recognition_reply_lost')
    return task
  },
  readRecognition: async (taskId, requestId) => {
    const reads = (recognitionReads.get(requestId) || 0) + 1
    recognitionReads.set(requestId, reads)
    const task = recognitionReceipts.get(requestId)
    return !task || task.id !== taskId || scene === 'recognition-unknown' && reads === 1 ? {task_id: taskId, request_id: requestId, state: 'unknown', input_revision: null, task: null} : {task_id: taskId, request_id: requestId, state: 'accepted', input_revision: task.input_revision!, task: structuredClone(scene === 'recognition-superseded' ? taskStore.get(taskId)! : task)}
  },
  endTask: async task => {
    const current = taskStore.get(task.id)
    if (!current || current.input_revision !== task.input_revision) throw Object.assign(new Error('preview_input_revision_conflict'), {status: 409})
    const next: AuditTask = {...task, state: 'completed', completion_reason: 'no_matching_rules'}
    taskStore.set(task.id, next)
    if (scene === 'unmatched-end-unknown') throw new Error('preview_end_reply_lost')
    return next
  },
  listTasks: async ({q, state, offset}) => {
    const tasks = [...taskStore.values()].filter(task => (!state || task.state === state) && `${task.number} ${task.files.map(row => row.file.original_filename).join(' ')}`.toLowerCase().includes(q.toLowerCase()))
    const page = tasks.slice(offset, offset + 20).map(task => scene === 'recognition-tasks-list-stale' && q ? recognitionReceipts.get(task.recognition_request_id || '') || task : task)
    return {tasks: structuredClone(page), next_offset: tasks.length > offset + 20 ? offset + 20 : null}
  },
}

function fixtureMatch(files: AuditTaskFile[], mode: AuditTask['analysis_mode'], customer: string | null, previous: AuditTask | null, requestId: string, number = previous?.number || requestId): AuditTask {
    const hasControlPlan = files.some(row => row.selection === 'selected' && driveCatalog.some(fixture => fixture.source.token === row.file.file_id && fixture.index === 1))
    const matchedFiles = structuredClone(files).map(row => {
      if (row.selection !== 'selected') return {...row, match: null}
      // Map fixture identities, never infer real audit answers from filenames.
      const index = row.file.file_id === 'preview-duplicate-document' && scene === 'reports-duplicate' ? 1 : driveCatalog.find(fixture => fixture.source.token === row.file.file_id)?.index ?? -1
      const document = documents[index]
      const known = Number.isInteger(index) && index >= 0 && Boolean(document)
      const matched = known && !scene.startsWith('unmatched') && !scene.endsWith('-unmatched') && !(scene === 'partial' && index === 2) && !(scene === 'recognition-progress-removal' && index === 0)
      const sampleChecks = reportSamples.documents[index]?.checks
      const rules = sampleChecks ? sampleChecks.map(check => ({id: check.id, category: check.category, priority: null, rule_to_check: check.requirement, type_of_rule: 'table'})) : [{id: `${index + 1}.1`, category: '频次一致性', priority: null, rule_to_check: '示例条款，仅用于界面走查', type_of_rule: 'table'}, {id: `${index + 1}.2`, category: '记录完整性', priority: null, rule_to_check: '示例：指定的检查记录必须完整且可读。', type_of_rule: 'table'}]
      const gaps = scene === 'matching-gaps' && matched && index === 0 && !hasControlPlan ? ['示例：未选择控制计划，频次一致性缺少跨文件比对资料。'] : []
      return {...row, match: {state: scene === 'error' ? 'failed' as const : matched ? 'matched' as const : 'unmatched' as const, metadata: {customer: '示例客户', model: '示例车型', part: '尾门导流板', part_number: 'P-204'}, document_type: known ? document.type : null, rule: matched && scene !== 'error' && !(scene === 'match-rule-missing' && index === 1) ? {id: `preview-rule-${index + 1}`, title: document.rule, body: {kind: 'engine' as const, type: document.type, rule_profile: 'v3', customers: ['示例客户'], models: [], parts: [], rules}} : null, gaps}}
    })
    const count = matchedFiles.filter(row => row.selection === 'selected' && row.match?.state === 'matched' && row.match.rule).length
    // Fixture display only. Real estimates remain Product API-owned.
    return {id: previous?.id || requestId, number, state: 'pending_confirm', analysis_mode: mode, created_at: previous?.created_at || '2026-10-16T00:00:00Z', updated_at: '2026-10-16T00:00:00Z', source_draft_id: null, round_id: null, customer_preference: customer, files: matchedFiles, completion_reason: null, estimated_points: scene === 'error' ? null : count * 500, input_revision: previous?.input_revision ?? 0, recognition_request_id: requestId}
}

const archiveAdapter = fixtureArchive(taskStore, scene, (id, sequence, failed, pending) => {
  const matched = fixtureMatch(adapter.samples(), 'standard', null, null, crypto.randomUUID())
  const seed = {...matched, id, number: `PPAP-历史示例-${sequence}`, files: matched.files.map((row, index) => ({...row, file: {...row.file, file_id: `${id}-file-${index}`, id: `${id}-version-${index}`}, match: row.match ? {...row.match, metadata: {...row.match.metadata, model: `第 ${sequence} 次车型`, part_number: `P-204-${sequence}`, part: sequence === 2 ? '旧版尾门导流板' : '尾门导流板'}} : null}))}
  const accepted = fixtureAcceptance(seed, `${id}-accept`)
  if (scene === 'archive-display') accepted.files = accepted.files.map(file => ({...file, display_info: {revision: 1, values: {...fileDisplayValues(file), customer: `第 ${sequence} 次显示客户`}}}))
  return pending ? accepted : fixtureResults(accepted, failed ? 'reports-none' : 'reports-ready')
}, sharingAdapter.forReport)
const usageAdapter = fixtureUsage(archiveAdapter, scene)

// A fixed read fixture, not persistence of arbitrary uploads or a second runtime API.
if (scene.startsWith('restore-') && scene !== 'restore-missing' || scene.startsWith('reports-')) {
  const files = adapter.samples().map((row, index) => ({...row, file: {...row.file, id: `preview-recovery-version-${index}`}}))
  const matched = fixtureMatch(files, 'standard', null, null, crypto.randomUUID())
  const seed: AuditTask = {...matched, id: scene.startsWith('reports-') ? 'preview-report-task' : 'preview-recovery-task', number: scene.startsWith('reports-') ? 'PPAP-报告示例' : 'PPAP-恢复示例', created_at: '2026-10-15T00:00:00Z', updated_at: '2026-10-15T00:00:00Z'}
  const frozen = fixtureAcceptance(seed, 'preview-recovery-request')
  const snapshot = scene === 'restore-pending' ? seed : scene === 'restore-matching' ? {...seed, state: 'matching' as const} : scene === 'restore-unknown' ? {...seed, acceptance: {request_id: 'preview-recovery-request', state: 'pending' as const, reason: null}} : (['restore-progress', 'restore-task-conflict', 'restore-usage-notice'].includes(scene) || automaticObservation || scene.startsWith('restore-estimate')) ? fixtureProgress(frozen) : scene === 'restore-report-conflict' ? fixtureResults(frozen, 'reports-generating') : scene === 'restore-completed' || scene === 'restore-usage-completed' || scene.startsWith('reports-') || scene.startsWith('restore-display-') ? fixtureResults(frozen, scene) : frozen
  if (scene.startsWith('restore-usage-')) snapshot.usage_notice = usageNotices.paid_insufficient
  if (scene.startsWith('restore-display-')) snapshot.files = snapshot.files.map(file => ({...file, display_info: {revision: 0, values: fileDisplayValues(file)}, display_edit_access: {state: 'allowed', reason: null}}))
  if (scene.startsWith('restore-estimate')) {
    const now = Date.now(), time = (minutes: number) => new Date(now + minutes * 60000).toISOString()
    snapshot.completion_estimate = {observed_at: time(0), earliest_at: time(10), latest_at: time(20)}
    if (scene === 'restore-estimate-overdue') snapshot.completion_estimate = {observed_at: time(-30), earliest_at: time(-20), latest_at: time(-10)}
    if (scene === 'restore-estimate-invalid') snapshot.completion_estimate.earliest_at = '2026-10-16T02:10:00'
    if (scene === 'restore-estimate-reversed') snapshot.completion_estimate.latest_at = time(5)
  }
  if (scene === 'restore-task-conflict' || scene === 'restore-report-conflict') {snapshot.state = 'completed'; snapshot.completion_reason = 'processed'; snapshot.progress = {completed: 3, total: 3}}
  if (scene === 'restore-observe-unresolved') {
    const control = fixtureResults(frozen, 'reports-generating').files.find(row => row.match?.document_type === '控制计划')!
    control.audit!.report.generation_request = {request_id: 'preview-original-generation-request', state: 'pending', reason: '示例：报告重生成受理结果未知，只读取原请求。'}
    snapshot.files = snapshot.files.map(row => row.file.id === control.file.id ? control : row)
    snapshot.progress = {completed: 1, total: 3}
  }
  taskStore.set(seed.id, snapshot)
  if (scene === 'restore-unknown') accepted.set(seed.id, frozen)
}

function Preview() {
  const [reads, setReads] = useState<{id: number; label: string; finish: () => void; fail: () => void}[]>([])
  const readNumber = useRef(0)
  const defer = useCallback(<T,>(label: string, result: T): Promise<T> => {
    return new Promise((resolve, reject) => {
      const id = ++readNumber.current
      setReads(rows => [...rows, {id, label, finish: () => resolve(result), fail: () => reject(new Error('preview_read_failed'))}])
    })
  }, [])
  const originalReads = useRef(new Map<string, number>())
  const originalOffice = useCallback<RemoteOriginalSource['office']>(async file => {
    const read = (originalReads.current.get(file.file_version_id) || 0) + 1
    originalReads.current.set(file.file_version_id, read)
    if (scene === 'original-error' && read === 1) throw new Error('preview_original_unavailable')
    const result = {available: false, notice: read === 1 ? '此文件暂不支持在线预览，请下载查看。' : '在线预览暂不可用，请下载查看。'}
    return scene === 'original-loading' ? defer(`原件配置 · ${file.original_filename} · 读取 ${read}`, result) : result
  }, [defer])
  useEffect(() => {
    const deliver = (event: MessageEvent) => {
      if (event.source !== window.parent || event.origin !== location.origin || event.data?.type !== 'ppap-preview-read') return
      const first = reads[0]
      if (!first) return
      if (event.data.failed) first.fail(); else first.finish()
      setReads(rows => rows.filter(row => row.id !== first.id))
    }
    window.addEventListener('message', deliver)
    return () => window.removeEventListener('message', deliver)
  }, [reads])
  const controlled: AuditIntakeAdapter = {...adapter,
    listTasks: async args => {
      const result = await adapter.listTasks(args)
      return scene === 'recognition-tasks-list-stale' && args.q ? defer('任务列表旧投影', result) : result
    },
    readValidation: async row => {
      const result = await adapter.readValidation(row)
      return scene === 'checking-loading' ? defer(`预检查结果 · ${row.file.original_filename}`, result) : result
    },
    upload: async (input, requestId) => {
      const result = await adapter.upload(input, requestId)
      return scene === 'upload-loading' ? defer(`传输回执 · ${input.file.name}`, result) : result
    },
    drive: {...adapter.drive, list: async pageToken => {
      const result = await adapter.drive.list(pageToken)
      return scene === 'drive-loading' ? defer('飞书文件列表', result) : result
    }},
    match: async (...args) => {
      const result = await adapter.match(...args)
      if (['recognition-loading', 'recognition-late', 'recognition-late-unmatched'].includes(scene)) return defer(`识别请求回执 · ${result.number}`, result)
      if (scene === 'recognition-rematch-late-accepted' && args[3]) {
        taskStore.set(result.id, fixtureAcceptance(result, 'preview-other-confirmation'))
        return defer(`旧识别请求回执 · ${result.number}`, result)
      }
      if (scene === 'recognition-progress-removal') return {...result, state: 'matching', files: result.files.map((file, index) => index === 1 && file.selection === 'selected' ? {...file, match: null} : file)}
      return ['matching', 'observe-matching'].includes(scene) ? {...result, state: 'matching', estimated_points: null, files: result.files.map((file, index) => file.selection === 'selected' ? {...file, match: {state: index === 0 ? 'matching' : 'pending', metadata: {customer: null, model: null, part: null}, document_type: null, rule: null, gaps: []}} : file)} : result
    },
    confirm: async (task, requestId) => {
      const result = await adapter.confirm(task, requestId)
      return scene === 'confirm-loading' ? defer(`受理回执 · ${task.number}`, result) : result
    },
    readTask: async (id, requestId) => {
      const task = taskStore.get(id)
      const result = await adapter.readTask(id, requestId)
      if (scene === 'recognition-progress-removal' && !task?.round_id && !requestId) return defer('其余文件识别结果', result)
      const job = result.report_package
      if (scene === 'reports-zip-foreign' && job?.state === 'ready' && !foreignPackageReads.has(job.receipt.request_id)) {
        foreignPackageReads.add(job.receipt.request_id)
        const reports = job.reports.map(reference => ({...reference, task_id: 'preview-other-task', round_id: 'preview-other-round'}))
        return {...result, report_package: {...job, reports, outcomes: reports.map(reference => ({reference, state: 'prepared' as const, reason: null}))}}
      }
      if (automaticObservation && (recoveryRead > 1 || scene === 'observe-matching')) return defer(`任务观察 · ${id} · 读取 ${scene === 'observe-matching' ? recoveryRead : recoveryRead - 1}`, result)
      if (scene === 'restore-race' && recoveryRead === 2 && task) return defer(`旧结果 · ${id}`, task)
      return scene === 'matching' && !task?.round_id && !requestId ? defer(`识别结果 · ${task?.number}`, result) : scene === 'audit-loading' && (task?.round_id || result.round_id) ? defer(`审核结果 · ${task?.number}`, result) : scene === 'restore-loading' && recoveryRead === 1 ? defer(`恢复任务 · ${id}`, result) : displayReadFixture(result, scene)
    },
  }
  const controlledReports = useMemo(() => ({...reportAdapter,
    readPdf: async (...args: Parameters<typeof reportAdapter.readPdf>) => {
      const result = await reportAdapter.readPdf(...args)
      return scene === 'reports-pdf-race' ? defer(`PDF · ${args[0].file_version_id}`, result) : result
    },
    regenerate: async (...args: Parameters<typeof reportAdapter.regenerate>) => {
      const result = await reportAdapter.regenerate(...args)
      return scene === 'reports-generate-race' ? defer(`报告生成回执 · ${args[0].file_version_id}`, result) : result
    },
  }), [defer])
  const controlledDisplay = useMemo(() => ({...displayAdapter, save: async (...args: Parameters<typeof displayAdapter.save>) => {
    const result = await displayAdapter.save(...args)
    return delayedDisplay ? defer(`显示保存回执 · ${args[0].reference.file_version_id}`, structuredClone(result)) : result
  }}), [defer])
  const controlledArchive = useMemo(() => ({...archiveAdapter,
    readHistory: async (...args: Parameters<typeof archiveAdapter.readHistory>) => {
      const result = await archiveAdapter.readHistory(...args)
      return scene === 'archive-history-race' && result.entry.sequence === 2 ? defer('历史审核 · 第 2 次', result) : result
    },
  }), [defer])
  const controlledSharing = useMemo(() => ({...sharingAdapter,
    listTargets: async (...args: Parameters<typeof sharingAdapter.listTargets>) => {
      const result = await sharingAdapter.listTargets(...args)
      return scene === 'reports-share-directory-loading' ? defer('分享目标目录', result) : result
    },
    send: async (...args: Parameters<typeof sharingAdapter.send>) => {
      const result = await sharingAdapter.send(...args)
      return scene === 'reports-share-race' ? defer('分享发送回执', result) : result
    },
  }), [defer])
  const controlledTemplates = useMemo(() => ({...templateAdapter,
    listTemplates: async (...args: Parameters<typeof templateAdapter.listTemplates>) => {
      const result = await templateAdapter.listTemplates(...args)
      return scene === 'templates-race' && args[0].customer_id === 'preview-customer-a' ? defer('模板清单 · 示例客户 A', result) : result
    },
  }), [defer])
  const controlledFeedback = useMemo(() => ({...feedbackAdapter,
    upload: async (...args: Parameters<typeof feedbackAdapter.upload>) => {
      const result = await feedbackAdapter.upload(...args)
      return scene === 'feedback-upload-loading' ? defer(`模板附件上传 · ${args[0].file.name}`, result) : result
    },
    submit: async (...args: Parameters<typeof feedbackAdapter.submit>) => {
      const result = await feedbackAdapter.submit(...args)
      return scene === 'feedback-submit-loading' ? defer('材料提报回执', result) : result
    },
  }), [defer])
  const controlledTemplateSharing = useMemo(() => ({...templateSharingAdapter,
    send: async (...args: Parameters<typeof templateSharingAdapter.send>) => {
      const result = await templateSharingAdapter.send(...args)
      return scene === 'templates-share-race' ? defer('模板分享回执', result) : result
    },
  }), [defer])
  const controlledPermissions = useMemo(() => ({...templatePermissionAdapter,
    apply: async (...args: Parameters<typeof templatePermissionAdapter.apply>) => {
      const result = await templatePermissionAdapter.apply(...args)
      return scene === 'templates-permissions-race' ? defer('权限修改回执', result) : result
    },
  }), [defer])
  const usageReads = useRef(0)
  const controlledUsage = useMemo(() => ({...usageAdapter,
    readUsage: async (...args: Parameters<typeof usageAdapter.readUsage>) => {
      const read = ++usageReads.current, result = await usageAdapter.readUsage(...args)
      return scene === 'usage-race' && read === 1 ? defer('旧用量查询', result) : result
    },
  }), [defer])
  const controlledSupport = useMemo(() => supportAdapter ? {...supportAdapter, read: async () => {
    const result = await supportAdapter.read()
    return scene === 'support-loading' ? defer('服务配置', result) : result
  }} : undefined, [defer])
  return <>{window.parent === window && <p role="note" className="border-b px-4 py-2 text-xs text-muted-foreground">模拟预览 · 不连接现网</p>}{(automaticObservation || delayedDisplay || scene.startsWith('recognition-') || ['checking-loading', 'matching', 'drive-loading', 'confirm-loading', 'audit-loading', 'upload-loading', 'restore-loading', 'restore-race', 'reports-pdf-race', 'reports-generate-race', 'reports-share-race', 'reports-share-directory-loading', 'archive-history-race', 'templates-race', 'feedback-submit-loading', 'feedback-upload-loading', 'templates-share-race', 'templates-permissions-race', 'usage-race', 'original-loading', 'support-loading'].includes(scene)) && <aside aria-label="预览事件控制" className="flex flex-wrap items-center gap-2 border-b bg-muted p-3 text-xs"><p>预览事件</p>{reads.map(read => <div key={read.id} className="flex flex-wrap items-center gap-2"><span>{read.label}</span><Button size="sm" variant="outline" onClick={() => {read.finish(); setReads(rows => rows.filter(row => row.id !== read.id))}}>返回示例读取 {read.id}</Button><Button size="sm" variant="outline" onClick={() => {read.fail(); setReads(rows => rows.filter(row => row.id !== read.id))}}>令示例读取 {read.id} 失败</Button></div>)}</aside>}
    <AuditIntake adapter={controlled} autoObserve={automaticObservation} reportAdapter={controlledReports} sharingAdapter={controlledSharing} archiveAdapter={controlledArchive} templateAdapter={controlledTemplates} templateSharingAdapter={controlledTemplateSharing} templatePermissionAdapter={controlledPermissions} usageAdapter={controlledUsage} supportAdapter={controlledSupport} feedbackAdapter={controlledFeedback} displayAdapter={scene.startsWith('restore-display-') ? controlledDisplay : undefined} renderOriginal={(file, evidence) => scene.startsWith('original-') && file.original_filename.endsWith('.xlsx') ? <FixtureRemoteOriginal key={file.id} file={file} office={originalOffice}/> : <FixtureOriginal file={file} evidence={evidence}/>}/></>
}
createRoot(document.getElementById('app')!).render(<Preview/>)

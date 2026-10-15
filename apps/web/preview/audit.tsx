import {createRoot} from 'react-dom/client'
import {useEffect, useRef, useState} from 'react'
import '../src/index.css'
import {AuditIntake, type AuditIntakeAdapter, type AuditTaskFile} from '../src/audit/intake'
import {Button} from '../src/components/ui/button'
import {fixtureAcceptance, fixtureResults, FixtureOriginal} from './audit-results'

// Explicit UI fixtures only. No production entry imports this adapter.
const query = new URLSearchParams(location.search)
const scene = query.get('scene') || 'populated'
document.documentElement.classList.toggle('dark', query.get('theme') === 'dark')
const documents = [
  {type: 'PFMEA', name: 'PFMEA.xlsx', rule: '示例 PFMEA 规则 · v1', mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
  {type: '控制计划', name: '控制计划.xlsx', rule: '示例控制计划规则 · v2', mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
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
const acceptanceReads = new Map<string, number>()
function sample(index: number): AuditTaskFile {
  const id = `preview-file-${crypto.randomUUID()}`
  const document = documents[index]
  return {file: {file_id: `preview-document-${index}`, id, original_filename: document.name, content_type: document.mime, byte_size: 240000 + index * 80000, status: 'ready', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: 'valid', reasons: []}, match: null}
}
const adapter: AuditIntakeAdapter = {
  drive: {
    list: async () => {
      driveReads += 1
      if (scene === 'drive-denied') throw new Error('authorization_required')
      if (scene === 'drive-error' && driveReads === 1) throw new Error('preview_read_failed')
      return {files: scene === 'drive-empty' ? [] : [...driveCatalog.map(row => row.source), {token: 'preview-image', name: '现场照片.jpg', type: 'file'}]}
    },
    receive: files => files.map(file => {
      const fixture = driveCatalog.find(row => row.source.token === file.token)
      if (!fixture) throw new Error('unknown_preview_source')
      const row = sample(fixture.index)
      return {...row, file: {...row.file, file_id: fixture.source.token, original_filename: fixture.filename, content_type: fixture.mime}, validation: {state: 'pending', reasons: ['示例飞书文件已选择，等待预检查。没有导入真实云端文件。']}}
    }),
  },
  samples: () => {
    const files = documents.map((_, index) => sample(index))
    if (scene === 'checking') {
      files[1].validation = {state: 'checking', reasons: ['示例：等待预检查结果']}
      files[2].validation = {state: 'invalid', reasons: ['示例：文件加密，不能读取']}
    }
    if (scene === 'invalid') files.push({...sample(0), file: {...sample(0).file, original_filename: '加密文件.xlsx'}, validation: {state: 'invalid', reasons: ['文件加密或异常，无法识别']}})
    return files
  },
  readValidation: async row => driveCatalog.some(fixture => fixture.source.token === row.file.file_id) ? {state: 'valid', reasons: []} : row.validation,
  confirm: async (task, requestId) => {
    const result = accepted.get(task.id) || fixtureAcceptance(task, requestId)
    if (scene === 'confirm-rule-conflict') {
      const rule = result.files[0].match?.rule
      if (rule?.body.kind === 'engine') rule.body.rules[0].rule_to_check = '同一规则 ID 的错误改写'
    }
    accepted.set(task.id, result)
    if (scene === 'confirm-unknown') throw new Error('preview_response_lost')
    return result
  },
  readTask: async task => {
    if (task.round_id) return fixtureResults(task, scene)
    const result = accepted.get(task.id)
    if (result) {
      const reads = (acceptanceReads.get(task.id) || 0) + 1
      acceptanceReads.set(task.id, reads)
      if (scene === 'confirm-unknown' && reads === 1) return {...task, acceptance: {request_id: task.acceptance!.request_id, state: 'pending', reason: null}}
      return result
    }
    return adapter.match(task.files, task.analysis_mode, task.customer_preference, task)
  },
  receive: files => files.map(file => {
    const reason = file.size > 200 * 1024 * 1024 ? '文件超过 200 MB 设计上限' : !/\.(xlsx|xls|xlsm|doc|docx|pdf)$/i.test(file.name) ? '非审核支持格式' : ''
    return {file: {file_id: `preview-local-${crypto.randomUUID()}`, id: `preview-local-version-${crypto.randomUUID()}`, original_filename: file.name, content_type: file.type, byte_size: file.size, status: 'local_preview', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: reason ? 'invalid' : 'pending', reasons: [reason || '待真实预检查：本预览只登记本机文件，尚未执行真实解析。']}, match: null}
  }),
  match: (files, mode, customer, previous) => {
    const matchedFiles = structuredClone(files).map(row => {
      if (row.selection !== 'selected') return {...row, match: null}
      // Map fixture identities, never infer real audit answers from filenames.
      const index = driveCatalog.find(fixture => fixture.source.token === row.file.file_id)?.index ?? -1
      const document = documents[index]
      const known = Number.isInteger(index) && index >= 0 && Boolean(document)
      const matched = known && scene !== 'unmatched' && !(scene === 'partial' && index === 2)
      return {...row, match: {state: scene === 'error' ? 'failed' as const : matched ? 'matched' as const : 'unmatched' as const, metadata: {customer: '示例客户', model: '示例车型', part: 'P-204'}, document_type: known ? document.type : null, rule: matched && scene !== 'error' ? {id: `preview-rule-${index + 1}`, title: document.rule, body: {kind: 'engine' as const, type: document.type, rule_profile: 'v3', customers: ['示例客户'], models: [], parts: [], rules: [{id: `${index + 1}.1`, category: '频次一致性', priority: null, rule_to_check: '示例条款，仅用于界面走查', type_of_rule: 'table'}, {id: `${index + 1}.2`, category: '记录完整性', priority: null, rule_to_check: '示例：指定的检查记录必须完整且可读。', type_of_rule: 'table'}]}} : null, gaps: []}}
    })
    const count = matchedFiles.filter(row => row.selection === 'selected' && row.match?.state === 'matched' && row.match.rule).length
    // Fixture display only. Real estimates remain Product API-owned.
    return {id: previous?.id || `preview-task-${crypto.randomUUID()}`, number: previous?.number || `PPAP-示例-${String(nextTask++).padStart(3, '0')}`, state: 'pending_confirm', analysis_mode: mode, created_at: previous?.created_at || new Date().toISOString(), updated_at: new Date().toISOString(), source_draft_id: null, round_id: null, customer_preference: customer, files: matchedFiles, completion_reason: null, estimated_points: scene === 'error' ? null : count * 500}
  },
}

function Preview() {
  const [reads, setReads] = useState<{id: number; label: string; finish: () => void; fail: () => void}[]>([])
  const readNumber = useRef(0)
  function defer<T>(label: string, result: T): Promise<T> {
    return new Promise((resolve, reject) => {
      const id = ++readNumber.current
      setReads(rows => [...rows, {id, label, finish: () => resolve(result), fail: () => reject(new Error('preview_read_failed'))}])
    })
  }
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
    drive: {...adapter.drive, list: async pageToken => {
      const result = await adapter.drive.list(pageToken)
      return scene === 'drive-loading' ? defer('飞书文件列表', result) : result
    }},
    match: (...args) => {
      const result = adapter.match(...args)
      return scene === 'matching' ? {...result, state: 'matching', estimated_points: null, files: result.files.map((file, index) => file.selection === 'selected' ? {...file, match: {state: index === 0 ? 'matching' : 'pending', metadata: {customer: null, model: null, part: null}, document_type: null, rule: null, gaps: []}} : file)} : result
    },
    confirm: async (task, requestId) => {
      const result = await adapter.confirm(task, requestId)
      return scene === 'confirm-loading' ? defer(`受理回执 · ${task.number}`, result) : result
    },
    readTask: task => scene === 'matching' && !task.round_id && !task.acceptance ? defer(`识别结果 · ${task.number}`, adapter.match(task.files, task.analysis_mode, task.customer_preference, task)) : scene === 'audit-loading' && task.round_id ? adapter.readTask(task).then(result => defer(`审核结果 · ${task.number}`, result)) : adapter.readTask(task),
  }
  return <>{['matching', 'drive-loading', 'confirm-loading', 'audit-loading'].includes(scene) && <aside aria-label="预览事件控制" className="flex flex-wrap items-center gap-2 border-b bg-muted p-3 text-xs"><p>模拟事件控制：手动返回结果，不使用定时器伪造进度。</p>{reads.map(read => <div key={read.id} className="flex flex-wrap items-center gap-2"><span>{read.label}</span><Button size="sm" variant="outline" onClick={() => {read.finish(); setReads(rows => rows.filter(row => row.id !== read.id))}}>返回示例读取 {read.id}</Button><Button size="sm" variant="outline" onClick={() => {read.fail(); setReads(rows => rows.filter(row => row.id !== read.id))}}>令示例读取 {read.id} 失败</Button></div>)}</aside>}<AuditIntake adapter={controlled} renderOriginal={(file, evidence) => <FixtureOriginal file={file} evidence={evidence}/>}/></>
}
createRoot(document.getElementById('app')!).render(<Preview/>)

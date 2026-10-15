import {createRoot} from 'react-dom/client'
import '../src/index.css'
import {AuditIntake, type AuditIntakeAdapter, type AuditTaskFile} from '../src/audit/intake'

// Explicit UI fixtures only. No production entry imports this adapter.
const query = new URLSearchParams(location.search)
const scene = query.get('scene') || 'populated'
document.documentElement.classList.toggle('dark', query.get('theme') === 'dark')
const documents = [
  {type: 'PFMEA', name: 'PFMEA.xlsx', rule: '示例 PFMEA 规则 · v1', mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
  {type: '控制计划', name: '控制计划.xlsx', rule: '示例控制计划规则 · v2', mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
  {type: '过程流程图', name: '过程流程图.pdf', rule: '示例过程流程图规则 · v3', mime: 'application/pdf'},
]
let nextTask = 1
function sample(index: number): AuditTaskFile {
  const id = `preview-file-${crypto.randomUUID()}`
  const document = documents[index]
  return {file: {file_id: `preview-document-${index}`, id, original_filename: document.name, content_type: document.mime, byte_size: 240000 + index * 80000, status: 'ready', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: 'valid', reasons: []}, match: null}
}
const adapter: AuditIntakeAdapter = {
  samples: () => {
    const files = documents.map((_, index) => sample(index))
    if (scene === 'invalid') files.push({...sample(0), file: {...sample(0).file, original_filename: '加密文件.xlsx'}, validation: {state: 'invalid', reasons: ['文件加密或异常，无法识别']}})
    return files
  },
  receive: files => files.map(file => {
    const reason = file.size > 200 * 1024 * 1024 ? '文件超过 200 MB 设计上限' : !/\.(xlsx|xls|xlsm|doc|docx|pdf)$/i.test(file.name) ? '非审核支持格式' : ''
    return {file: {file_id: `preview-local-${crypto.randomUUID()}`, id: `preview-local-version-${crypto.randomUUID()}`, original_filename: file.name, content_type: file.type, byte_size: file.size, status: 'local_preview', sha256: null}, selection: 'selected', removal_reason: null, validation: {state: reason ? 'invalid' : 'pending', reasons: [reason || '待真实预检查：本预览只登记本机文件，尚未执行真实解析。']}, match: null}
  }),
  match: (files, mode, customer, previous) => {
    const matchedFiles = structuredClone(files).map(row => {
      if (row.selection !== 'selected') return {...row, match: null}
      // Map fixture identities, never infer real audit answers from filenames.
      const index = Number(row.file.file_id.replace('preview-document-', ''))
      const document = documents[index]
      const known = Number.isInteger(index) && index >= 0 && Boolean(document)
      const matched = known && scene !== 'unmatched' && !(scene === 'partial' && index === 2)
      return {...row, match: {state: scene === 'error' ? 'failed' as const : matched ? 'matched' as const : 'unmatched' as const, metadata: {customer: '示例客户', model: '示例车型', part: 'P-204'}, document_type: known ? document.type : null, rule: matched && scene !== 'error' ? {id: `preview-rule-${index + 1}`, title: document.rule, body: {kind: 'engine' as const, type: document.type, rule_profile: 'v3', customers: ['示例客户'], models: [], parts: [], rules: [{id: `${index + 1}.1`, category: '示例', priority: null, rule_to_check: '示例条款，仅用于界面走查', type_of_rule: 'table'}]}} : null, gaps: []}}
    })
    const count = matchedFiles.filter(row => row.selection === 'selected' && row.match?.state === 'matched' && row.match.rule).length
    const estimates: Record<number, number> = {0: 0, 1: 500, 2: 1000, 3: 1500}
    return {id: previous?.id || `preview-task-${crypto.randomUUID()}`, number: previous?.number || `PPAP-示例-${String(nextTask++).padStart(3, '0')}`, state: 'pending_confirm', analysis_mode: mode, created_at: previous?.created_at || new Date().toISOString(), updated_at: new Date().toISOString(), source_draft_id: null, round_id: null, customer_preference: customer, files: matchedFiles, completion_reason: null, estimated_points: scene === 'error' ? null : estimates[count] ?? null}
  },
}

createRoot(document.getElementById('app')!).render(<AuditIntake adapter={adapter}/>)

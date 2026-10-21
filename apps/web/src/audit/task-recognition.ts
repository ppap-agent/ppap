import {useRef, useState} from 'react'
import {requestTrace} from '../api.ts'
import type {components} from '../generated/openapi'
import {sameSnapshot} from './result-projection.ts'
import {rememberRequest} from './routes.ts'

type Task = components['schemas']['AuditTask']
export type AuditRecognitionRead = components['schemas']['AuditRecognitionRead']
export type AuditRecognitionAdapter = {
  match: (files: Task['files'], mode: Task['analysis_mode'], customer: string | null, previous: Task | null, requestId: string, predecessorId?: string) => Promise<Task>
  readRecognition: (taskId: string, requestId: string) => Promise<AuditRecognitionRead>
}
type RecognitionInput = {files: Task['files']; mode: Task['analysis_mode']; customer: string | null; previous: Task | null; predecessorId?: string}
export type RecognitionObservation = {requestId: string; taskId: string; phase: 'sending' | 'reading' | 'unknown' | 'accepted' | 'rejected' | 'conflict'; message: string; traceId?: string; currentRevision?: number; currentState?: Task['state']}
const unknownMessage = '识别请求结果尚未确认。文件与选择已保留；请读取原请求回执，不要重复开始识别。'

function acceptedInput(input: RecognitionInput, next: Task, requestId: string, taskId: string) {
  const revision = input.previous ? (input.previous.input_revision ?? 0) + 1 : 0
  return next.id === taskId && next.recognition_request_id === requestId && next.input_revision === revision && next.analysis_mode === input.mode && next.customer_preference === input.customer && (next.predecessor_id || null) === (input.predecessorId || null) && !next.round_id && ['matching', 'pending_confirm'].includes(next.state) && next.files.length === input.files.length && input.files.every(file => next.files.some(row => sameSnapshot(row.file, file.file) && row.selection === file.selection && row.removal_reason === file.removal_reason && row.validation_id === file.validation_id && row.preferred_rule_version_id === file.preferred_rule_version_id && Boolean(row.reference_only) === Boolean(file.reference_only) && (file.selection !== 'selected' || row.replacement_of_file_version_id === file.replacement_of_file_version_id)))
}

/** Keeps each recognition command and its immutable input until its receipt is known. */
export function createTaskRecognition(adapter: AuditRecognitionAdapter, onAccepted: (task: Task, key: string) => void, onObservation: (key: string, observation: RecognitionObservation) => void = () => {}) {
  const operations = new Map<string, {observation: RecognitionObservation; input: RecognitionInput}>()
  function remember(key: string, input: RecognitionInput, observation: RecognitionObservation) {
    const operation = {input, observation}
    operations.set(key, operation); onObservation(key, observation)
    return operation
  }
  async function run(key: string, input: RecognitionInput, readOnly: boolean) {
    const current = operations.get(key)
    if (readOnly ? current?.observation.phase !== 'unknown' : current && !['accepted', 'rejected', 'conflict'].includes(current.observation.phase)) return
    const requestId = readOnly ? current!.observation.requestId : crypto.randomUUID()
    const trace = readOnly && current!.observation.traceId ? {traceId: current!.observation.traceId} : {}
    const frozen = readOnly ? current!.input : structuredClone(input)
    if (!readOnly) frozen.files = frozen.files.map(file => ({...file, removal_reason: file.removal_reason?.trim() || null}))
    const taskId = frozen.previous?.id || requestId
    if (!readOnly) rememberRequest(taskId, 'recognitionRequest', requestId)
    const operation = remember(key, frozen, {requestId, taskId, ...trace, phase: readOnly ? 'reading' : 'sending', message: readOnly ? current!.observation.message : ''})
    try {
      const receipt = readOnly ? await adapter.readRecognition(taskId, requestId) : null
      const next = readOnly ? receipt?.task : await adapter.match(structuredClone(frozen.files), frozen.mode, frozen.customer, structuredClone(frozen.previous), requestId, frozen.predecessorId)
      if (operations.get(key) !== operation) return
      const acceptedRevision = frozen.previous ? (frozen.previous.input_revision ?? 0) + 1 : 0
      if (receipt?.state === 'accepted' && receipt.request_id === requestId && receipt.task_id === taskId && receipt.input_revision === acceptedRevision && next?.id === taskId && ((next.input_revision ?? -1) > acceptedRevision || next.input_revision === acceptedRevision && ['reviewing', 'completed'].includes(next.state))) {
        remember(key, frozen, {requestId, taskId, phase: 'conflict', currentRevision: next.input_revision, currentState: next.state, message: '原识别请求已受理，但任务已在其他页面更新。当前文件与选择已保留；请先读取当前任务。'})
      } else if (next && (!receipt || receipt.request_id === requestId && receipt.task_id === taskId && receipt.state === 'accepted' && receipt.input_revision === next.input_revision) && acceptedInput(frozen, next, requestId, taskId)) {
        remember(key, frozen, {requestId, taskId, phase: 'accepted', message: ''}); onAccepted(next, key)
      } else remember(key, frozen, {requestId, taskId, ...trace, phase: 'unknown', message: unknownMessage})
    } catch (failure) {
      if (operations.get(key) !== operation) return
      const status = failure instanceof Error && 'status' in failure ? failure.status : null
      const rejected = !readOnly && (status === 400 || status === 409)
      const traceId = requestTrace(failure) || trace.traceId
      remember(key, frozen, {requestId, taskId, ...(traceId ? {traceId} : {}), phase: rejected ? 'rejected' : 'unknown', message: rejected ? '本次识别请求已明确拒绝。文件与选择已保留；请核对输入，再明确重新开始识别。' : unknownMessage})
    }
  }
  return {
    get: (key: string) => operations.get(key)?.observation,
    start: (key: string, input: RecognitionInput) => run(key, input, false),
    read: (key: string) => {const current = operations.get(key); return current ? run(key, current.input, true) : Promise.resolve()},
  }
}

export function useTaskRecognition(adapter: AuditRecognitionAdapter, onAccepted: (task: Task, key: string) => void) {
  const latest = useRef({adapter, onAccepted}); latest.current = {adapter, onAccepted}
  const [observations, setObservations] = useState<Record<string, RecognitionObservation>>({})
  const controller = useRef<ReturnType<typeof createTaskRecognition> | null>(null)
  if (!controller.current) controller.current = createTaskRecognition({match: (...args) => latest.current.adapter.match(...args), readRecognition: (...args) => latest.current.adapter.readRecognition(...args)}, (task, key) => latest.current.onAccepted(task, key), (key, observation) => setObservations(rows => ({...rows, [key]: observation})))
  return {...controller.current, observations}
}

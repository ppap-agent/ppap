import {useRef, useState} from 'react'
import {requestTrace} from '../api.ts'
import type {components} from '../generated/openapi'
import {sameFrozenFileInfo, sameSnapshot} from './result-projection.ts'
import {rememberRequest} from './routes.ts'

type Task = components['schemas']['AuditTask']
export type AuditAcceptanceAdapter = {
  confirm: (task: Task, requestId: string) => Promise<Task>
  readTask: (taskId: string, requestId?: string) => Promise<Task>
}
type Observation = {staleBasisDigest?: string; requestId: string; phase: 'sending' | 'unknown' | 'reading' | 'accepted' | 'rejected'; message: string; traceId?: string}

function acceptedSnapshot(before: Task, after: Task, requestId: string) {
  if (after.id !== before.id || after.acceptance?.request_id !== requestId || after.acceptance.state !== 'accepted' || !after.round_id || !['reviewing', 'completed'].includes(after.state) || after.analysis_mode !== before.analysis_mode) return false
  if (after.state === 'completed' ? after.completion_reason !== 'processed' : after.completion_reason !== null) return false
  const expected = before.files.filter(file => file.selection === 'selected' && (file.match?.state === 'reference' || file.match?.state === 'matched' && file.match.rule))
  const actual = after.files.filter(file => file.selection === 'selected')
  return expected.some(file => file.match?.state === 'matched') && actual.length === expected.length && expected.every(file => actual.some(next => sameFrozenFileInfo(file, next) && next.match?.state === file.match?.state && sameSnapshot(next.match?.rule, file.match?.rule) && (file.match?.state === 'reference' ? !next.audit : !next.audit || sameSnapshot(next.audit.rule, file.match?.rule) && next.audit.file_version_id === file.file.id && next.audit.round_id === after.round_id))) && after.files.length === before.files.length && after.files.every(file => before.files.some(previous => sameSnapshot(previous.file, file.file)) && (file.selection === 'selected' || !file.audit))
}

/** A lost response retains its request identity. Only an explicit rejection permits another command. */
export function createTaskAcceptance(adapter: AuditAcceptanceAdapter, onAccepted: (task: Task) => void, onObservation: (id: string, observation: Observation) => void = () => {}) {
  const operations = new Map<string, Observation>()
  const inputs = new Map<string, Task>()
  function remember(id: string, observation: Observation) {
    operations.set(id, observation)
    onObservation(id, observation)
  }
  function needsBasisRefresh(task: Task) {
    const current = operations.get(task.id)
    return current?.phase === 'rejected' && current.staleBasisDigest !== undefined && current.staleBasisDigest === task.confirmation_basis_digest
  }
  async function run(task: Task, readOnly: boolean) {
    const current = operations.get(task.id)
    if (readOnly ? current?.phase !== 'unknown' : !task.confirmation_basis_digest || needsBasisRefresh(task) || Boolean(current && current.phase !== 'rejected')) return
    const requestId = readOnly ? current!.requestId : crypto.randomUUID()
    if (!readOnly) rememberRequest(task.id, 'acceptRequest', requestId)
    const trace = readOnly && current!.traceId ? {traceId: current!.traceId} : {}
    if (!readOnly) inputs.set(requestId, structuredClone(task))
    const input = inputs.get(requestId)!
    const observation: Observation = {requestId, ...trace, phase: readOnly ? 'reading' : 'sending', message: readOnly ? current!.message : ''}
    remember(task.id, observation)
    try {
      const next = await (readOnly ? adapter.readTask(input.id, requestId) : adapter.confirm(structuredClone(input), requestId))
      if (operations.get(task.id) !== observation) return
      if (acceptedSnapshot(input, next, requestId)) {
        remember(task.id, {requestId, phase: 'accepted', message: ''}); onAccepted(next)
      } else if (next.id === task.id && next.state === 'pending_confirm' && !next.round_id && next.acceptance?.request_id === requestId && next.acceptance.state === 'rejected') {
        remember(task.id, {requestId, phase: 'rejected', ...(input.confirmation_basis_digest && next.confirmation_basis_digest !== input.confirmation_basis_digest ? {staleBasisDigest: input.confirmation_basis_digest} : {}), message: next.acceptance.reason || '本次请求明确未受理，可以重新确认。'})
      } else {
        const reason = next.id === task.id && next.acceptance?.request_id === requestId && next.acceptance.state === 'pending' ? next.acceptance.reason : null
        remember(task.id, {requestId, ...trace, phase: 'unknown', message: reason || '受理结果尚未确认。请读取同一请求的回执，不要重复开始审核。'})
      }
    } catch (failure) {
      const traceId = requestTrace(failure) || trace.traceId
      if (operations.get(task.id) === observation) remember(task.id, {requestId, ...(traceId ? {traceId} : {}), phase: 'unknown', message: '未取得受理回执。审核可能已经开始；请读取受理结果，不要重复确认。'})
    }
  }
  function suspend(taskId: string) {
    const current = operations.get(taskId)
    if (current && ['sending', 'reading'].includes(current.phase)) remember(taskId, {...current, phase: 'unknown', message: '正在恢复任务，先读取原请求结果，不要重复开始审核。'})
  }
  function restore(task: Task) {
    if (task.acceptance?.state === 'accepted') {
      const requestId = task.acceptance.request_id, current = operations.get(task.id), input = inputs.get(requestId)
      if (!task.round_id || !['reviewing', 'completed'].includes(task.state) || current && current.requestId !== requestId || input && !acceptedSnapshot(input, task, requestId)) return false
      remember(task.id, {requestId, phase: 'accepted', message: ''})
      return true
    }
    if (!task.acceptance) return true
    const current = operations.get(task.id)
    if (current && current.requestId !== task.acceptance.request_id) return true
    if (task.acceptance.state === 'rejected' && task.state === 'pending_confirm' && !task.round_id) {
      const input = inputs.get(task.acceptance.request_id)
      remember(task.id, {requestId: task.acceptance.request_id, phase: 'rejected', ...(input?.confirmation_basis_digest && task.confirmation_basis_digest !== input.confirmation_basis_digest ? {staleBasisDigest: input.confirmation_basis_digest} : {}), message: task.acceptance.reason || '本次请求明确未受理，可以重新确认。'})
      return true
    }
    if (current || task.acceptance.state !== 'pending') return true
    const requestId = task.acceptance.request_id
    inputs.set(requestId, structuredClone(task))
    remember(task.id, {requestId, phase: 'unknown', message: task.acceptance.reason || '恢复到尚未取得回执的确认请求。请读取原请求结果，不要重复开始审核。'})
    return true
  }
  return {get: (id: string) => operations.get(id), needsBasisRefresh, restore, suspend, confirm: (task: Task) => run(task, false), reconcile: (task: Task) => run(task, true)}
}

export function useTaskAcceptance(adapter: AuditAcceptanceAdapter, onAccepted: (task: Task) => void) {
  const latest = useRef({adapter, onAccepted}); latest.current = {adapter, onAccepted}
  const [observations, setObservations] = useState<Record<string, Observation>>({})
  const controller = useRef<ReturnType<typeof createTaskAcceptance> | null>(null)
  if (!controller.current) controller.current = createTaskAcceptance({confirm: (...args) => latest.current.adapter.confirm(...args), readTask: (...args) => latest.current.adapter.readTask(...args)}, task => latest.current.onAccepted(task), (id, observation) => setObservations(rows => ({...rows, [id]: observation})))
  return {...controller.current, observations}
}

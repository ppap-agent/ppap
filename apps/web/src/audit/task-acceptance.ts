import {useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {sameSnapshot} from './result-projection'

type Task = components['schemas']['AuditTask']
export type AuditAcceptanceAdapter = {
  confirm: (task: Task, requestId: string) => Promise<Task>
  readTask: (task: Task) => Promise<Task>
}
type Observation = {requestId: string; phase: 'sending' | 'unknown' | 'reading' | 'accepted' | 'rejected'; message: string}

function acceptedSnapshot(before: Task, after: Task, requestId: string) {
  if (after.id !== before.id || after.acceptance?.request_id !== requestId || after.acceptance.state !== 'accepted' || !after.round_id || !['reviewing', 'completed'].includes(after.state) || after.analysis_mode !== before.analysis_mode) return false
  if (after.state === 'completed' ? after.completion_reason !== 'processed' : after.completion_reason !== null) return false
  const expected = before.files.filter(file => file.selection === 'selected' && file.match?.state === 'matched' && file.match.rule)
  const actual = after.files.filter(file => file.selection === 'selected')
  return expected.length > 0 && actual.length === expected.length && expected.every(file => actual.some(next => sameSnapshot(next.file, file.file) && sameSnapshot(next.match?.rule, file.match?.rule) && (!next.audit || sameSnapshot(next.audit.rule, file.match?.rule) && next.audit.file_version_id === file.file.id && next.audit.round_id === after.round_id))) && after.files.length === before.files.length && after.files.every(file => before.files.some(previous => sameSnapshot(previous.file, file.file)) && (file.selection === 'selected' || !file.audit))
}

/** A lost response retains its request identity. Only an explicit rejection permits another command. */
export function useTaskAcceptance(adapter: AuditAcceptanceAdapter, onAccepted: (task: Task) => void) {
  const operations = useRef(new Map<string, Observation>())
  const inputs = useRef(new Map<string, Task>())
  const [observations, setObservations] = useState<Record<string, Observation>>({})
  function remember(id: string, observation: Observation) {
    operations.current.set(id, observation)
    setObservations(rows => ({...rows, [id]: observation}))
  }
  async function run(task: Task, readOnly: boolean) {
    const current = operations.current.get(task.id)
    if (readOnly ? current?.phase !== 'unknown' : Boolean(current && current.phase !== 'rejected')) return
    const requestId = readOnly ? current!.requestId : crypto.randomUUID()
    if (!readOnly) inputs.current.set(requestId, structuredClone(task))
    const input = inputs.current.get(requestId)!
    remember(task.id, {requestId, phase: readOnly ? 'reading' : 'sending', message: ''})
    try {
      const next = await (readOnly ? adapter.readTask({...structuredClone(input), acceptance: {request_id: requestId, state: 'pending', reason: null}}) : adapter.confirm(structuredClone(input), requestId))
      if (operations.current.get(task.id)?.requestId !== requestId) return
      if (acceptedSnapshot(input, next, requestId)) {
        remember(task.id, {requestId, phase: 'accepted', message: ''}); onAccepted(next)
      } else if (next.id === task.id && next.state === 'pending_confirm' && !next.round_id && next.acceptance?.request_id === requestId && next.acceptance.state === 'rejected') {
        remember(task.id, {requestId, phase: 'rejected', message: next.acceptance.reason || '本次请求明确未受理，可以重新确认。'})
      } else {
        remember(task.id, {requestId, phase: 'unknown', message: '受理结果尚未确认。请读取同一请求的回执，不要重复开始审核。'})
      }
    } catch {
      if (operations.current.get(task.id)?.requestId === requestId) remember(task.id, {requestId, phase: 'unknown', message: '未取得受理回执。审核可能已经开始；请读取受理结果，不要重复确认。'})
    }
  }
  return {observations, confirm: (task: Task) => run(task, false), reconcile: (task: Task) => run(task, true)}
}

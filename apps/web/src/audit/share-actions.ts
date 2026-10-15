import {useEffect, useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {sameSnapshot} from './result-projection'
import {targetKey, type AuditSharingAdapter, type ShareRequest, type ReportShare, type ShareTarget} from './sharing-contract'

export type ShareContentFor = (reports: components['schemas']['AuditReportReference'][], mode: 'link' | 'file') => components['schemas']['AuditShareContent'] | null
export const shareUnresolved = (job?: ReportShare | null) => Boolean(job && (job.receipt.state === 'pending' || job.deliveries.some(row => ['pending', 'unknown'].includes(row.state))))
export const shareRetryAllowed = (job: ReportShare, delivery: ReportShare['deliveries'][number]) => job.receipt.state === 'accepted' && delivery.state === 'failed' && delivery.retry_allowed && delivery.target.eligibility === 'eligible' && delivery.target.organization_id === job.request.organization_id

/** Closing the dialog does not cancel the operation or erase its request. */
export function useShareActions(job: ReportShare | null | undefined, onChange: (job: ReportShare) => void, contentFor: ShareContentFor, adapter?: AuditSharingAdapter) {
  const current = useRef(job), change = useRef(onChange), alive = useRef(true), reading = useRef(false)
  current.current = job; change.current = onChange
  const [busyRead, setBusyRead] = useState(false), [error, setError] = useState('')
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  function save(job: ReportShare) {
    if (!alive.current) return
    current.current = job; change.current(job)
  }
  async function observe(observation: ReportShare, operation: () => Promise<ReportShare>) {
    save(observation); setError('')
    try {
      const update = await operation()
      if (!alive.current || current.current !== observation) return
      if (!sameSnapshot(update.request, observation.request) || update.receipt.request_id !== observation.request.request_id || update.deliveries.length !== observation.deliveries.length) throw new Error('share_receipt_mismatch')
      const expected = new Map(observation.deliveries.map(row => [targetKey(row.target), row]))
      if (new Set(update.deliveries.map(row => targetKey(row.target))).size !== expected.size || update.deliveries.some(row => {
        const before = expected.get(targetKey(row.target))
        return !before || row.target.organization_id !== update.request.organization_id || row.attempt_id !== before.attempt_id || before.state === 'sent' && row.state !== 'sent'
      })) throw new Error('share_delivery_mismatch')
      save(update)
    } catch {
      if (alive.current && current.current === observation) setError('未取得可靠发送回执。保留原请求与已确认结果，只读取发送结果，不重复发送。')
    }
  }
  async function send(input: Omit<ShareRequest, 'request_id'>, targets: ShareTarget[]) {
    if (!adapter || shareUnresolved(current.current)) return
    if (!sameSnapshot(contentFor(input.reports, input.content.kind === 'link' ? 'link' : 'file'), input.content) || !targets.length || new Set(targets.map(targetKey)).size !== targets.length || targets.some(target => target.eligibility !== 'eligible' || target.organization_id !== input.organization_id) || !sameSnapshot(input.targets, targets.map(({kind, id}) => ({kind, id})))) {setError('报告产物或接收范围不再匹配。未发送，请关闭后重新核对。'); return}
    const request = structuredClone({...input, request_id: crypto.randomUUID()})
    const observation: ReportShare = {request, receipt: {request_id: request.request_id, state: 'pending', reason: '发送受理结果尚未知。'}, deliveries: targets.map(target => ({target: structuredClone(target), attempt_id: request.request_id, state: 'unknown', reason: '尚未取得本次发送回执。', retry_allowed: false}))}
    await observe(observation, () => adapter.send(structuredClone(request)))
  }
  async function read() {
    const job = current.current
    if (!adapter || !job || reading.current) return
    reading.current = true; setBusyRead(true)
    try {await observe(structuredClone(job), () => adapter.read({request_id: job.request.request_id, attempts: job.deliveries.map(row => ({target: {kind: row.target.kind, id: row.target.id}, attempt_id: row.attempt_id}))}))}
    finally {reading.current = false; if (alive.current) setBusyRead(false)}
  }
  async function retry(target: components['schemas']['AuditShareTargetReference']) {
    const job = current.current, delivery = job?.deliveries.find(row => targetKey(row.target) === targetKey(target))
    if (!adapter || !job || !delivery || !shareRetryAllowed(job, delivery)) return
    const attemptId = crypto.randomUUID()
    const observation: ReportShare = {...job, deliveries: job.deliveries.map(row => row === delivery ? {...row, attempt_id: attemptId, state: 'unknown', retry_allowed: false, reason: '重试回执未知，只读取本次发送结果。'} : row)}
    await observe(observation, () => adapter.retryFailed(job.request.request_id, {kind: target.kind, id: target.id}, attemptId))
  }
  return {send, read, retry, busyRead, error}
}

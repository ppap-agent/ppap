import {useEffect, useRef, useState, type SetStateAction} from 'react'
import {deepOffered, request, type Draft, type DraftSaveRequest, type Material, type Round, type QuotaEstimate} from '../api'
import {useAccess} from './access'
import {acceptanceKey, draftPlan, message, readAcceptance, sameAcceptedPlan, useRemote} from './remote'
import type {CheckPlan, CheckPreparationState, CheckRequest} from './check-preparation-state'

export type LiveCheck = {busy: boolean; error: string; ready: boolean; start: () => void; reconcile: () => void; deepOffered: boolean; targets: string[]; quota: QuotaEstimate | null; quotaError: string; refreshQuota: () => void}
export function useLiveAudit({id, registration, items, state, plan, onChange, ready = true, beforeStart}: {
  id: string; registration: object; items: Material[]; state: CheckPreparationState; plan: CheckPlan | null
  onChange: (state: SetStateAction<CheckPreparationState | undefined>) => void
  ready?: boolean
  beforeStart?: () => Promise<void>
}) {
  const access = useAccess(), enabled = Boolean(access?.principal && !access.preview)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [roundId, setRoundId] = useState(state.request?.phase === 'accepted' ? state.request.runId : '')
  const working = useRef(false), latest = useRef({registration, state, plan, items}); latest.current = {registration, state, plan, items}
  const remote = useRemote<Round>(enabled && roundId ? `/rounds/${roundId}` : null, 3000)
  const quota = useRemote<QuotaEstimate>(enabled && state.request?.phase !== 'accepted' && items.some(item => item.role === 'target') ? `/integrations/feishu/usage/quota?target_count=${items.filter(item => item.role === 'target').length}` : null)
  const key = enabled ? acceptanceKey(access!.principal!.user_id, id) : ''
  function accepted(round: Round, requestRecord?: CheckRequest) {
    const current = latest.current
    const record = requestRecord ?? current.state.request
    setRoundId(round.id); remote.setValue(round)
    if (record) onChange({...current.state, request: {...record, runId: round.id, phase: 'accepted', remote: 'accepted', queryError: false}})
  }
  async function reconcile() {
    if (!enabled || working.current) return
    working.current = true; setBusy(true); setError('')
    try {
      const round = await request<Round | null>(`/drafts/${id}/round`)
      if (round) {
        const pending = readAcceptance(key)
        if (pending && !sameAcceptedPlan(round, id, pending.plan)) throw new Error('服务器轮次与原检查请求不一致，请返回工作台核对。')
        accepted(round)
      } else if (latest.current.state.request) onChange(current => current ? {...current, request: current.request ? {...current.request, phase: 'absent', remote: 'absent', queryError: false} : null} : current)
    } catch (cause) {setError(message(cause))}
    finally {working.current = false; setBusy(false)}
  }
  useEffect(() => {if (enabled && !roundId) void reconcile()}, [id, enabled])
  async function start() {
    if (!enabled || !ready || working.current || !latest.current.plan) return
    working.current = true; setBusy(true); setError('')
    try {
      await beforeStart?.()
      const current = latest.current, selected = current.items
      if (!selected.some(item => item.role === 'target')) throw new Error('没有与本次要求类型对应的待审文件，请核对文件分类或审核规则。')
      if (selected.some(item => item.role === 'target' && !item.rule_version_id)) throw new Error('请先确认并保存本次审核规则。')
      const pending = readAcceptance(key)
      const requestedPlan = draftPlan({items: selected, analysis_mode: current.state.mode})
      if (pending && pending.plan !== requestedPlan) throw new Error('原检查请求尚未完成核对，不能改变其文件或规则。')
      const idempotency = pending?.key ?? crypto.randomUUID()
      const record: CheckRequest = {id: idempotency, runId: '', plan: structuredClone(current.plan!), attempt: (current.state.request?.attempt || 0) + 1, at: new Date().toISOString(), phase: 'requesting', remote: 'pending', queryError: false}
      localStorage.setItem(key, JSON.stringify({key: idempotency, plan: requestedPlan}))
      const checking = {...current.state, request: record}
      onChange(checking)
      const body: DraftSaveRequest = {analysis_mode: current.state.mode, items: selected.map(item => ({file_version_id: item.file_version_id, role: item.role, rule_version_id: item.role === 'target' ? item.rule_version_id : null})), case_context: {registration: JSON.stringify({...current.registration, check: checking})}}
      await request<Draft>(`/drafts/${id}`, 'PUT', body)
      const round = await request<Round>(`/drafts/${id}/accept`, 'POST', {idempotency_key: idempotency})
      if (!sameAcceptedPlan(round, id, requestedPlan)) throw new Error('接收结果与本次方案不同，请核对原请求。')
      accepted(round, record)
    } catch (cause) {
      setError(message(cause))
      onChange(current => current?.request ? {...current, request: {...current.request, phase: 'unknown'}} : current)
    } finally {working.current = false; setBusy(false)}
  }
  return {round: remote.value, roundError: remote.error, refresh: remote.refresh, setRound: remote.setValue, enabled, live: enabled ? {busy, ready, error: error || (!ready ? '部分选中文件尚未上传到服务器，请重新上传后再开始审核。' : ''), start: () => void start(), reconcile: () => void reconcile(), deepOffered, targets: items.filter(item => item.role === 'target').map(item => item.original_filename), quota: quota.value, quotaError: quota.error, refreshQuota: quota.refresh} satisfies LiveCheck : undefined}
}

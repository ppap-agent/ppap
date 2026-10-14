// Stitch: projects/13261964020862199279/screens/a0ccdfe74bcc48698d9bc2f4e599ae2e.
// Reuses the existing reassessment owner. No second queue or consumption ledger.
import {useEffect, useState} from 'react'
import {Button} from '../src/components/ui/button'
import {Checkbox} from '../src/components/ui/checkbox'
import type {DecisionInput} from './decision'
import {loadDraft, useSavedDraft} from './saved-draft'
import {validChecks, type Checks} from './reassessment'
import {CheckPreparation} from './check-preparation'
import {applyCheckCoverage, initialCheck, recoverCheck, type CheckPreparationState} from './check-preparation-state'
import {demoCheckPlan, demoEvidence} from './demo-check'
import type {DemoDocument} from './source-documents'

export const receiptInputKey = (input: DecisionInput) => JSON.stringify([input.receiptId, input.scope, input.basis, input.files, input.clarification, input.receipts])
const checkBusy = (round: Checks['rounds'][string] | undefined) => Boolean(round?.state === 'running' || round?.state === 'prepared' && round.check?.request && round.check.request.phase !== 'absent')
export function receiptReviewState(round: Checks['rounds'][string] | undefined, applied?: string) {
  const busy = checkBusy(round)
  const token = round?.input && round.check?.request ? `${round.check.request.runId}:${round.attempt}` : ''
  const awaitingReview = Boolean(!busy && token && token !== applied)
  return {busy, token, awaitingReview, pending: busy || awaitingReview}
}
export function readReceiptCheck(caseId: string, decisionId: string, input: DecisionInput, applied?: string) {
  const loaded = loadDraft(`reassessment:${caseId}`, validChecks)
  return {...receiptReviewState(loaded.saved?.value.rounds[`reply:${decisionId}:${input.receiptId}`], applied), error: loaded.error}
}
export function useReceiptRecheck(caseId: string, decisionId: string, input: DecisionInput, documents: DemoDocument[]) {
  const {field, saveError} = useSavedDraft<Checks>(`reassessment:${caseId}`, () => ({rounds: {}}), validChecks)
  const [rounds, setRounds] = field('rounds')
  const roundId = `reply:${decisionId}:${input.receiptId}`
  const round = rounds[roundId]
  const [preparing, setPreparing] = useState(new URLSearchParams(location.search).get('checkRound') === roundId)
  const [fail, setFail] = useState(false)
  const {busy, token} = receiptReviewState(round)
  useEffect(() => {
    setRounds(current => Object.fromEntries(Object.entries(current).map(([key, value]) => [key, value.check ? {...value, check: recoverCheck(value.check)} : value])))
    const url = new URL(location.href)
    if (url.searchParams.get('checkRound') === roundId) {url.searchParams.delete('checkRound'); history.replaceState(null, '', url)}
  }, [])
  useEffect(() => {
    if (saveError || !round?.receiptSource) return
    if (round.state === 'prepared' && round.check?.request?.phase === 'accepted') {
      setRounds(current => current[roundId]?.state === 'prepared' && current[roundId].check?.request?.phase === 'accepted' ? {...current, [roundId]: {...current[roundId], state: 'running', attempt: 1}} : current)
      return
    }
    if (round.state !== 'running') return
    const timer = window.setTimeout(() => {
      const source = round.receiptSource!
      let evidence = applyCheckCoverage(demoEvidence(source.input, source.documents), round.check?.request)
      if (round.fail) {
        const first = evidence.find(row => !['不适用', '未覆盖'].includes(row.result))?.id
        evidence = evidence.map(row => row.id === first ? {...row, result: '执行失败', explanation: '本条示例检查中断；其他条目结果保留。', sources: []} : row)
      }
      setRounds(current => current[roundId]?.state === 'running' && current[roundId].attempt === round.attempt
        ? {...current, [roundId]: {...current[roundId], state: round.fail ? 'failed' : 'done', input: {...source.input, evidence}}} : current)
    }, 700)
    return () => window.clearTimeout(timer)
  }, [round, roundId, saveError])
  function prepare() {
    if (saveError || busy || !input.basis?.confirmed) return
    if (!round) setRounds(current => ({...current, [roundId]: {state: 'prepared', input: null, attempt: 0, check: initialCheck(), receiptSource: structuredClone({decisionId, input, documents}), fail}}))
    setPreparing(true)
  }
  function retry() {
    if (saveError || round?.state !== 'failed') return
    setRounds(current => current[roundId]?.state === 'failed' ? {...current, [roundId]: {...current[roundId], state: 'running', attempt: current[roundId].attempt + 1, fail}} : current)
  }
  function change(next: React.SetStateAction<CheckPreparationState | undefined>) {
    setRounds(current => {
      const target = current[roundId]
      if (!target?.check) return current
      const check = typeof next === 'function' ? next(target.check) : next
      return check ? {...current, [roundId]: {...target, check}} : current
    })
  }
  return {caseId, roundId, round, busy, token, preparing, setPreparing, fail, setFail, prepare, retry, change, saveError}
}
export function ReceiptCheckPreparation({controller}: {controller: ReturnType<typeof useReceiptRecheck>}) {
  const {round, caseId, roundId, change, setPreparing, saveError} = controller
  const source = round?.receiptSource
  const plan = source && round.check && demoCheckPlan(source.input, source.documents, round.check.mode)
  return plan && round?.check ? <CheckPreparation caseId={caseId} state={round.check} plan={plan} saveError={saveError} recheck={{owner: {kind: 'reassessment', roundId}, description: '本批回件复查', backLabel: '返回复核', note: source!.input.receipts?.at(-1)?.notes ?? ''}} onChange={change} onHome={() => setPreparing(false)} onContinue={() => setPreparing(false)}/> : null
}
export function ReceiptCheckActions({controller: c, applied, disabled, onApply}: {controller: ReturnType<typeof useReceiptRecheck>; applied?: string; disabled: boolean; onApply: (input: DecisionInput, token: string) => void}) {
  return <section className="gf-receipt-check" aria-label="本批检查"><div><h2>本批检查</h2><p role="status">{c.busy ? c.round?.state === 'running' ? '正在检查本批资料，尚不能作出决定。' : '检查受理待核对。' : c.round?.input ? c.round.state === 'failed' ? '检查部分失败。可复核已有结果，或重试本次检查。' : c.token === applied ? '检查结果已进入当前复核。' : '检查完成，结果尚未进入人工复核。' : '回件已登记，尚未检查。也可直接依据原件进行人工审核。'}</p></div>
    {c.saveError && <p role="alert">{c.saveError}</p>}
    <div className="gf-follow-actions">{(!c.round || c.round.state === 'prepared') && <Button disabled={disabled || Boolean(c.saveError)} onClick={() => c.busy ? c.setPreparing(true) : c.prepare()}>{c.busy ? '核对受理状态' : '准备本批检查'}</Button>}
      {c.round?.state === 'failed' && <Button variant="outline" disabled={disabled || Boolean(c.saveError)} onClick={c.retry}>重试本次检查</Button>}
      {c.round?.check?.request && c.round.state !== 'prepared' && <Button variant="outline" onClick={() => c.setPreparing(true)}>检查方案</Button>}
      {c.round?.input && !c.busy && c.token !== applied && <Button disabled={disabled || Boolean(c.saveError)} onClick={() => onApply(c.round!.input!, c.token)}>使用本次结果复核</Button>}
    </div>
    {c.round?.input && !c.busy && c.token !== applied && <p>结果变化的条目需重新确认；本批中依据和结果未变的判断保留。不会自动判定问题解决。</p>}
    {import.meta.env.DEV && (!c.round || ['prepared', 'failed'].includes(c.round.state)) && <details><summary>演示执行情形</summary><label className="gf-follow-check"><Checkbox checked={c.fail} disabled={disabled || c.busy} onCheckedChange={value => c.setFail(value === true)}/>下一次模拟一项检查失败</label></details>}
  </section>
}

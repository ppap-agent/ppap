// Reference: Stitch projects/13261964020862199279/screens/f2caf9c26cb14d30aa76e53f5110f904.
import {useState, type ReactNode} from 'react'
import {ArrowRight, LoaderCircle, RefreshCw} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Textarea} from '../components/ui/textarea'
import {isKnownDemoClause, type RequirementBasis} from './requirements-state'
import type {DecisionEvidence} from './decision'
import {modeName, type CheckRequest} from './check-preparation-state'
import {roundInput} from './product'
import type {Round} from '../api'
import {serverJudgments} from './review-record'
import {judgmentComplete} from './judgment-state'
import {isPreview} from './access'
import {Badge} from '../components/ui/badge'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'

type Scenario = 'running' | 'clarification' | 'failure' | 'complete'
export type AuditSnapshot = {
  fileCount: number
  hasFlow: boolean
  hasControl: boolean
  flowUnreadable: boolean
  dimensionRevision: string | null
}

const scenarios: {id: Scenario; label: string}[] = [
  {id: 'running', label: '审核中'},
  {id: 'clarification', label: '需澄清'},
  {id: 'failure', label: '局部失败'},
  {id: 'complete', label: '待复核'},
]

function fromRoute<K extends string>(key: string, values: {id: K}[], fallback: K): K {
  const value = new URLSearchParams(window.location.search).get(key)
  return values.some(item => item.id === value) ? value as K : fallback
}

function previewResults(snapshot: AuditSnapshot, scenario: Scenario): string[] {
  const process = !snapshot.hasFlow || !snapshot.hasControl
    ? '未覆盖'
    : scenario === 'failure'
      ? '执行失败'
      : scenario === 'running'
        ? '核对中'
        : '发现不一致'
  const scan = !snapshot.hasFlow
    ? '未覆盖'
    : snapshot.flowUnreadable
      ? '无法判断'
      : '原件可读'
  const dimension = snapshot.dimensionRevision === 'B' ? '待检查内容' : '未覆盖'
  return [process, scan, dimension]
}

export function ProgressPrototype({snapshot, clarification, answer, onAnswerChange, onClarification, onOpenReview, savedScenario, onScenario, basis, completedEvidence, checkRequest, onCheckPlan, round, context, title, files, remoteError, onRefresh, onHome, onPrepare, preparing}: {
  snapshot: AuditSnapshot; clarification: string | null; answer: string
  onAnswerChange: (answer: string) => void; onClarification: (answer: string) => void; onOpenReview: () => void
  savedScenario?: Scenario; onScenario?: (scenario: Scenario) => void
  basis: RequirementBasis | null; completedEvidence?: DecisionEvidence[]; checkRequest?: CheckRequest; onCheckPlan?: () => void
  round?: Round | null; context?: string; title?: string; files?: ReactNode; remoteError?: string; onRefresh?: () => void; onHome?: () => void
  onPrepare?: () => void; preparing?: boolean
}) {
  const [scenarioState, setScenarioState] = useState(() => savedScenario ?? fromRoute('scenario', scenarios, 'running'))
  const canClarify = snapshot.hasFlow && snapshot.flowUnreadable && Boolean(basis?.clauses.some(clause => clause.id === 'R2' && clause.applicability === 'apply' && isKnownDemoClause(clause)))
  const scenario = round ? round.execution === 'completed' ? 'complete' : round.execution === 'failed' ? 'failure' : 'running' : scenarioState === 'clarification' && !canClarify ? 'complete' : scenarioState
  const presets = previewResults(snapshot, scenario)
  const input = round ? roundInput(round, basis, context || '', clarification) : null
  const judgments = round && input ? serverJudgments(round, input) : {}
  const reviewable = input?.evidence.filter(row => row.id !== 'no-result' && row.result !== '不适用') ?? []
  const reviewed = input ? reviewable.filter(row => judgmentComplete(input, row, judgments[row.id])).length : 0
  const allReviewed = reviewable.length > 0 && reviewed === reviewable.length
  const rows = input ? input.evidence.filter(row => row.id !== 'no-result') : basis?.clauses.map(clause => {
    const completed = completedEvidence?.find(row => row.id === clause.id), fallback = presets[Number(clause.id.slice(1)) - 1]
    return {id: clause.id, result: clause.applicability === 'exclude' ? '不适用' : clause.applicability !== 'apply' || !isKnownDemoClause(clause) ? '未覆盖' : scenario === 'running' ? '核对中' : scenario === 'failure' && clause.id === 'R1' ? '执行失败' : completed?.result ?? fallback ?? '未覆盖'}
  }) ?? []
  const headline = round?.execution === 'queued' ? '审核已接收，等待执行' : scenario === 'running' ? '正在审核资料' : scenario === 'clarification' ? clarification ? '说明已记录，待核实' : '需要核实原件' : scenario === 'failure' ? '审核未能完成' : allReviewed ? '人工复核已记录' : round && !rows.length ? '未获得逐项检查结果' : '检查结束，待人工复核'
  const groups = ['不满足', '无法判断', '未覆盖', '执行失败', '满足'].map(result => ({result, count: rows.filter(row => row.result === result).length})).filter(item => item.count)
  function simulate(next: Scenario) {setScenarioState(next); onScenario?.(next)}
  return <section className="mx-auto flex max-w-3xl flex-col gap-6 py-2 sm:py-4">
    <header className="space-y-2"><div className="flex flex-wrap items-start justify-between gap-3"><h1 className="min-w-0 break-all text-2xl font-semibold tracking-tight">{title || '本轮审核'}</h1><Badge variant="outline">{modeName(round?.analysis_mode || checkRequest?.plan.mode || 'standard')}</Badge></div>{context && <p className="break-words text-sm text-muted-foreground">{context}</p>}{files}</header>
    {remoteError && <Alert><AlertTitle>状态更新中断</AlertTitle><AlertDescription><p>{remoteError}</p><p>下方保留上次读取的状态。</p>{onRefresh && <Button variant="outline" size="sm" onClick={onRefresh}><RefreshCw/>重新读取状态</Button>}</AlertDescription></Alert>}
    <div className="space-y-4 border-t pt-6"><div className="flex items-center gap-2" role="status">{scenario === 'running' && !remoteError && <LoaderCircle aria-hidden="true" className="size-5 shrink-0 animate-spin motion-reduce:animate-none"/>}<h2 className="text-xl font-semibold">{headline}</h2></div>{scenario === 'failure' ? <p className="break-words text-sm">{round?.failure?.message || '部分检查未完成，已有结果和资料仍可查看。'}</p> : scenario === 'clarification' ? <p className="text-sm">流程图工序号无法辨认，请核对原件或登记收到的说明。</p> : scenario === 'running' ? <div className="space-y-2 text-sm text-muted-foreground">{round?.progress && <p>{({reading: '读取资料', planning: '建立审核计划', checking: '执行检查', validating: '核验证据'}[round.progress.phase])}</p>}<p>可以返回工作台，稍后继续。</p></div> : <p className="text-sm text-muted-foreground">{allReviewed ? '逐项判断已保存，不等同于问题已解决或正式批准。' : round && !rows.length ? '请核对本轮资料与审核依据，不能将空结果视为通过。' : '查看原件，逐项记录人工判断。'}</p>}
      {groups.length > 0 && scenario !== 'running' && <div className="flex flex-wrap gap-2" role="group" aria-label="机器检查结果">{groups.map(item => <Badge key={item.result} variant={item.result === '满足' ? 'secondary' : 'outline'}>{item.result} {item.count}</Badge>)}</div>}
      {round?.execution === 'running' && round.progress?.completed_count != null && <p className="text-sm tabular-nums">已检查 {round.progress.completed_count}{round.progress.total_count != null ? `/${round.progress.total_count}` : ''} 项</p>}
      {round?.execution === 'completed' && reviewable.length > 0 && <p className="text-sm tabular-nums text-muted-foreground">人工复核已保存 {reviewed}/{reviewable.length} 项</p>}
    </div>
    {scenario === 'clarification' && !clarification && <div className="grid gap-3"><label className="grid gap-1.5 text-sm">供应商说明<Textarea value={answer} onChange={event => onAnswerChange(event.target.value)} placeholder="记录收到的原话及其指向的原件位置"/></label><Button className="w-fit" disabled={!answer.trim()} onClick={() => onClarification(answer.trim())}>记录说明</Button></div>}
    {clarification && <blockquote className="whitespace-pre-wrap break-words border-l pl-4 text-sm">{clarification}</blockquote>}
    <div className="flex flex-wrap items-center gap-3">
      {scenario === 'complete' && (!round || rows.length > 0) && <Button onClick={onOpenReview}>{allReviewed ? '查看人工复核' : reviewed ? '继续复核' : '开始复核'}<ArrowRight/></Button>}
      {scenario === 'running' && round && onHome && <Button onClick={onHome}>返回工作台<ArrowRight/></Button>}
      {scenario === 'failure' && rows.length > 0 && <Button onClick={onOpenReview}>查看已有结果<ArrowRight/></Button>}
      {round?.execution === 'failed' && round.preparation_allowed && onPrepare && <Button variant="outline" disabled={preparing} onClick={onPrepare}>{preparing ? '正在准备新一轮…' : '准备新一轮审核'}<ArrowRight/></Button>}
      {!round && scenario === 'running' && <Button onClick={() => simulate(canClarify ? 'clarification' : 'complete')}>模拟继续检查<ArrowRight/></Button>}
      {!round && scenario === 'clarification' && clarification && <Button onClick={() => simulate('complete')}>模拟继续核验<ArrowRight/></Button>}
      {!round && scenario === 'failure' && <Button onClick={() => simulate('running')}>模拟重试</Button>}
      {onCheckPlan && <Button variant={round && !rows.length && scenario !== 'running' ? 'default' : 'outline'} onClick={onCheckPlan}>本轮资料与依据</Button>}
      {scenario === 'failure' && !remoteError && onRefresh && <Button variant="ghost" onClick={onRefresh}><RefreshCw/>重新读取状态</Button>}
    </div>
    {isPreview() && import.meta.env.DEV && <NativeSelect aria-label="预览审核状态" value={scenarioState} onChange={event => simulate(event.target.value as Scenario)}>{scenarios.map(item => <NativeSelectOption key={item.id} value={item.id}>{item.label}</NativeSelectOption>)}</NativeSelect>}
  </section>
}

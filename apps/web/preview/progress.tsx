// Three throwaway variants of the internal audit-progress page, on the intake route via ?step=progress&variant=.
import {useEffect, useState} from 'react'
import {ArrowLeft, ArrowRight, CircleAlert, CircleCheck, CircleDashed, FileQuestion, FileText, RotateCcw} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Textarea} from '../src/components/ui/textarea'
import {BasisSummary} from './requirements'
import {isKnownDemoClause, type RequirementBasis} from './requirements-state'
import './progress.css'
import type {DecisionEvidence} from './decision'
import {modeName, type CheckRequest} from './check-preparation-state'
import {requestUsage, usageHref} from './usage-state'

type Variant = 'action' | 'timeline' | 'ledger'
type Scenario = 'running' | 'clarification' | 'failure' | 'complete'
export type AuditSnapshot = {
  fileCount: number
  hasFlow: boolean
  hasControl: boolean
  flowUnreadable: boolean
  dimensionRevision: string | null
}
type Finding = {name: string; status: string; detail: string; tone: 'active' | 'attention' | 'failure' | 'neutral'}

const variants: {id: Variant; label: string}[] = [
  {id: 'action', label: '行动优先'},
  {id: 'timeline', label: '进度优先'},
  {id: 'ledger', label: '要求优先'},
]
const scenarios: {id: Scenario; label: string}[] = [
  {id: 'running', label: '审核中'},
  {id: 'clarification', label: '需澄清'},
  {id: 'failure', label: '局部失败'},
  {id: 'complete', label: '待复核'},
]
const steps = ['识别资料', '建立关联', '检查要求', '核验证据']

function fromRoute<K extends string>(key: string, values: {id: K}[], fallback: K): K {
  const value = new URLSearchParams(window.location.search).get(key)
  return values.some(item => item.id === value) ? value as K : fallback
}

function Status({tone, children}: {tone: Finding['tone']; children: React.ReactNode}) {
  return <span className={`gf-progress-tag gf-progress-tag--${tone}`}>{children}</span>
}

function findings(snapshot: AuditSnapshot, scenario: Scenario): Finding[] {
  const process: Finding = !snapshot.hasFlow || !snapshot.hasControl
    ? {name: '流程与控制计划一致性', status: '未覆盖', detail: '缺少参与比较的原件，不能给出一致性结论。', tone: 'neutral'}
    : scenario === 'failure'
      ? {name: '流程与控制计划一致性', status: '执行失败', detail: '跨文件比较未完成；技术失败不是业务不符合，也不是通过。', tone: 'failure'}
      : scenario === 'running'
        ? {name: '流程与控制计划一致性', status: '核对中', detail: '正在比较同一工序的两份原件。', tone: 'active'}
        : {name: '流程与控制计划一致性', status: '发现不一致', detail: '工序 17：流程图“热处理”，控制计划“表面处理”；待审核员复核原文。', tone: 'attention'}
  const scan: Finding = !snapshot.hasFlow
    ? {name: '流程图工序号可核对性', status: '未覆盖', detail: '本轮未提交过程流程图。', tone: 'neutral'}
    : snapshot.flowUnreadable
      ? {name: '流程图工序号可核对性', status: '无法判断', detail: '第 3 页有一处工序号无法可靠辨认；说明或补件后仍需核实。', tone: 'attention'}
      : {name: '流程图工序号可核对性', status: '原件可读', detail: '清晰度问题已排除；这不代表工艺要求通过。', tone: 'neutral'}
  const dimension: Finding = snapshot.dimensionRevision === 'B'
    ? {name: '尺寸报告覆盖图纸 Rev B', status: '待检查内容', detail: '报告修订与本轮图纸对应；实测尺寸仍需正式审核。', tone: 'active'}
    : {name: '尺寸报告覆盖图纸 Rev B', status: '未覆盖', detail: snapshot.dimensionRevision ? `现有报告基于 Rev ${snapshot.dimensionRevision}；不能证明 Rev B 的尺寸结果。` : '本轮未提交尺寸报告。', tone: 'neutral'}
  return [process, scan, dimension]
}

export function ProgressPrototype({snapshot, clarification, answer, onAnswerChange, onClarification, onOpenReview, sameCase, savedScenario, onScenario, basis, receiptId, completedEvidence, checkRequest, onCheckPlan}: {
  snapshot: AuditSnapshot
  clarification: string | null
  answer: string
  onAnswerChange: (answer: string) => void
  onClarification: (answer: string) => void
  onOpenReview: () => void
  sameCase: boolean
  savedScenario?: Scenario
  onScenario?: (scenario: Scenario) => void
  basis: RequirementBasis | null
  receiptId: string
  completedEvidence?: DecisionEvidence[]
  checkRequest?: CheckRequest
  onCheckPlan?: () => void
}) {
  const [variant, setVariant] = useState(() => fromRoute('variant', variants, 'action'))
  const [scenarioState, setScenarioState] = useState(() => savedScenario ?? fromRoute('scenario', scenarios, 'running'))
  const answerSent = clarification !== null
  const canClarify = snapshot.hasFlow && snapshot.flowUnreadable && Boolean(basis?.clauses.some(clause => clause.id === 'R2' && clause.applicability === 'apply' && isKnownDemoClause(clause)))
  const scenario = scenarioState === 'clarification' && !canClarify ? 'complete' : scenarioState
  const presetRows = findings(snapshot, scenario)
  const rows: Finding[] = !basis?.confirmed ? [{name: '审核依据', status: '未检查', detail: '没有已确认的条款快照，不能套用预设结果。', tone: 'neutral'}] : basis.clauses.map(clause => {
    if (clause.applicability === 'exclude') return {name: `${clause.id} · ${clause.title}`, status: '不适用', detail: clause.reason, tone: 'neutral'}
    if (clause.applicability !== 'apply' || !isKnownDemoClause(clause)) return {name: `${clause.id} · ${clause.title}`, status: '未覆盖', detail: '此条款没有对应的模拟执行结果。正文、出处或适用性变化后，不能沿用原结论。', tone: 'neutral'}
    const completed = completedEvidence?.find(row => row.id === clause.id)
    if (completed) {
      const failed = scenario === 'failure' && clause.id === 'R1'
      return {name: `${clause.id} · ${clause.title}`, status: scenario === 'running' ? '核对中' : failed ? '执行失败' : completed.result, detail: scenario === 'running' ? '正在核对本次文件与要求。' : failed ? '尚未完成核对，不形成业务结论。' : completed.explanation, tone: scenario === 'running' ? 'active' : failed ? 'failure' : completed.result === '满足' ? 'neutral' : 'attention'}
    }
    return {...presetRows[Number(clause.id.slice(1)) - 1], name: `${clause.id} · ${clause.title}`}
  })
  const current = scenarios.find(item => item.id === scenario)!
  const needsAnswer = scenario === 'clarification' && canClarify
  const headline = scenario === 'running' ? '正在核对本轮资料' : scenario === 'clarification' ? answerSent ? '说明已记录，仍待核实' : '需要联系供应商核实原件' : scenario === 'failure' ? '一项检查未能完成' : '检查结束，等待人工复核'
  const lead = scenario === 'running' ? '已确认资料归属，正在检查要求与原件。尚未形成整包结论。' : scenario === 'clarification' ? '流程图第 3 页的工序号不清晰。可通过现有渠道请供应商澄清；其它要求继续检查。' : scenario === 'failure' ? '检查发生技术故障，未完成部分不能形成结论。可重试后继续。' : '逐项机器结果已列在下方。请复核原文和适用要求，再作出本次范围的决定。'

  useEffect(() => {
    const sync = () => {
      setVariant(fromRoute('variant', variants, 'action'))
      // Browser back changes the view, not the saved execution result.
      if (!sameCase) setScenarioState(fromRoute('scenario', scenarios, 'running'))
    }
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [])
  function updateRoute(next: Partial<{variant: Variant; scenario: Scenario}>) {
    if (next.variant) setVariant(next.variant)
    if (next.scenario) {setScenarioState(next.scenario); onScenario?.(next.scenario)}
    const url = new URL(window.location.href)
    url.searchParams.set('step', 'progress')
    url.searchParams.set('variant', next.variant ?? variant)
    url.searchParams.set('scenario', next.scenario ?? scenario)
    window.history.pushState(null, '', url)
  }
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
      if ((event.target as HTMLElement)?.closest('input, textarea, select, button, [contenteditable="true"]')) return
      const index = variants.findIndex(item => item.id === variant)
      updateRoute({variant: variants[(index + (event.key === 'ArrowRight' ? 1 : variants.length - 1)) % variants.length].id})
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })
  function setScenario(next: Scenario) {
    updateRoute({scenario: next})
    window.scrollTo({top: 0, behavior: 'smooth'})
  }

  const attention = <section className={`gf-progress-attention gf-progress-attention--${scenario}`} aria-label="当前下一步">
    <div className="gf-progress-attention-head"><Status tone={scenario === 'failure' ? 'failure' : scenario === 'complete' ? 'neutral' : scenario === 'running' ? 'active' : 'attention'}>{current.label}</Status><span>{scenario === 'running' ? '系统检查中' : '审核团队处理'}</span></div>
    <h2>{headline}</h2><p>{lead}</p>
    {needsAnswer && !answerSent && <div className="gf-progress-reply"><label htmlFor="gf-progress-answer">登记收到的供应商说明</label><Textarea id="gf-progress-answer" placeholder="收到回复后记录其可核对的位置与原话；没有回复可以继续处理其它要求" value={answer} onChange={event => onAnswerChange(event.target.value)}/><div><span>记录说明不会修改原件，也不会自动解除“无法判断”。</span><Button className="gf-primary" disabled={!answer.trim()} onClick={() => onClarification(answer.trim())}>记录外部答复</Button></div></div>}
    {needsAnswer && answerSent && <div className="gf-progress-answer"><CircleCheck size={18}/><div><strong>供应商说明已记录</strong><p>{clarification}</p><small>此项仍为无法判断，等待审核员核对原件。</small></div></div>}
    <div className="gf-progress-attention-actions">
      {scenario === 'running' && <><Button className="gf-primary" onClick={() => setScenario(canClarify ? 'clarification' : 'complete')}>模拟继续检查 <ArrowRight size={16}/></Button><button onClick={() => setScenario('failure')}>查看局部失败情形</button></>}
      {scenario === 'clarification' && answerSent && <Button className="gf-primary" onClick={() => setScenario('complete')}>模拟继续核验 <ArrowRight size={16}/></Button>}
      {scenario === 'failure' && <Button className="gf-primary" onClick={() => setScenario('running')}><RotateCcw size={16}/> 模拟重试该检查</Button>}
      {scenario === 'complete' && <button onClick={onOpenReview}>{sameCase ? '开始本次人工复核' : '查看独立复核演示'} <ArrowRight size={16}/></button>}
    </div>
  </section>

  const pipeline = <section className="gf-progress-section" aria-label="审核步骤"><div className="gf-progress-section-heading"><h2>审核步骤</h2><span>步骤完成不等于审核通过</span></div><ol className="gf-progress-pipeline">{steps.map((step, index) => {
    const state = index < 2 ? 'done' : scenario === 'running' ? index === 2 ? 'active' : 'pending' : scenario === 'failure' ? index === 2 ? 'failed' : 'pending' : scenario === 'clarification' ? index === 2 ? 'done' : 'waiting' : 'done'
    return <li className={`gf-progress-step gf-progress-step--${state}`} key={step}><span className="gf-progress-step-icon">{state === 'done' ? <CircleCheck size={19}/> : state === 'failed' ? <CircleAlert size={19}/> : <CircleDashed size={19}/>}</span><div><strong>{step}</strong><small>{state === 'done' ? '已结束' : state === 'active' ? '进行中' : state === 'failed' ? '执行失败' : state === 'waiting' ? '等待澄清' : '待开始'}</small></div></li>
  })}</ol></section>

  const findingsSection = <section className="gf-progress-section" aria-label="逐项检查状态"><div className="gf-progress-section-heading"><h2>逐项检查</h2><span>{basis?.clauses.length ?? 0} 条要求</span></div><div className="gf-progress-findings">{rows.map(row => <div className="gf-progress-finding" key={row.name}><div><strong>{row.name}</strong><p>{row.detail}</p></div><Status tone={row.tone}>{row.status}</Status></div>)}</div></section>

  const activity = <section className="gf-progress-section" aria-label="已确认的工作记录"><div className="gf-progress-section-heading"><h2>工作记录</h2><span>只记可核对事件</span></div><div className="gf-progress-events"><div><CircleCheck size={17}/><span>审核员已登记 {snapshot.fileCount} 份原件{basis?.confirmed ? `，固定 ${basis.source.title} ${basis.source.version}` : '；要求快照待补全'}</span></div><div><CircleCheck size={17}/><span>已确认文件类型与事项归属</span></div>{snapshot.dimensionRevision !== 'B' && <div><FileQuestion size={17}/><span>尺寸报告未覆盖图纸 Rev B，未判为测量不合格</span></div>}{scenario === 'failure' && <div><CircleAlert size={17}/><span>跨文件比较执行失败，尚无一致性结论</span></div>}{answerSent && <div><FileText size={17}/><span>审核员已记录供应商说明；原件仍需核实</span></div>}</div></section>

  return <div className="gf-progress-page">
    <header className="gf-page-heading gf-progress-heading"><div><div className="gf-eyebrow">本次收件 · 检查进度</div><h1>支架 P-204</h1><p className="gf-case-meta">北辰汽车 <span>·</span> 明川精密 <span>·</span> 图纸 Rev B <span>·</span> 苏州工厂</p></div><span className="gf-round">{receiptId}</span></header>
    <div className="gf-progress-snapshot"><span>原件 {snapshot.fileCount} 份</span><span>{basis?.confirmed ? `已固定 ${basis.source.version}` : '要求未确认'}</span></div>
    {checkRequest && <div className="gf-check-run"><strong>{modeName(checkRequest.plan.mode)}</strong><span>{checkRequest.runId}</span><span>{requestUsage(checkRequest).actual !== null ? `已结算 ${requestUsage(checkRequest).actual} 点` : `预留 ${checkRequest.plan.estimate.high} 点 · 待结算`}</span><Button variant="ghost" size="sm" onClick={onCheckPlan}>查看检查方案</Button><Button variant="link" size="sm" asChild><a href={usageHref()}>查看账号用量</a></Button></div>}
    <BasisSummary basis={basis}/>
    {import.meta.env.DEV && <div className="gf-progress-scenarios" aria-label="模拟状态切换">{scenarios.map(item => <button key={item.id} aria-pressed={scenario === item.id} disabled={checkRequest?.settlement?.phase === 'confirmed' || item.id === 'clarification' && !canClarify} onClick={() => setScenario(item.id)}>{item.label}</button>)}</div>}
    {variant === 'action' && <div className="gf-progress-layout gf-progress-layout--action"><div>{attention}{findingsSection}</div><div>{pipeline}{activity}</div></div>}
    {variant === 'timeline' && <div className="gf-progress-layout gf-progress-layout--timeline">{pipeline}<div className="gf-progress-timeline-content">{attention}{activity}</div>{findingsSection}</div>}
    {variant === 'ledger' && <div className="gf-progress-layout gf-progress-layout--ledger"><div>{findingsSection}{activity}</div>{attention}{pipeline}</div>}
    {import.meta.env.DEV && <div className="gf-switcher" aria-label="进度页方案切换"><button aria-label="上一种布局" onClick={() => updateRoute({variant: variants[(variants.findIndex(item => item.id === variant) + variants.length - 1) % variants.length].id})}><ArrowLeft size={16}/></button><span>布局方案　{variants.findIndex(item => item.id === variant) + 1} / 3 · {variants.find(item => item.id === variant)?.label}</span><button aria-label="下一种布局" onClick={() => updateRoute({variant: variants[(variants.findIndex(item => item.id === variant) + 1) % variants.length].id})}><ArrowRight size={16}/></button></div>}
    <span className="gf-state-readout" aria-live="polite">模拟审核状态：{current.label} · {variant} · {answerSent ? '说明已记录' : '无补充说明'}</span>
  </div>
}

// Stitch reference: projects/13261964020862199279/screens/f7010f9f1e8d46cea63cf81187a74afa.
// Recheck reference: projects/13261964020862199279/screens/dfcb12c262bf4b569b0fe852b749ffd5.
// Three structures within the same receipt: coverage desk, plan first, short brief.
import {useEffect, useRef, useState, type SetStateAction} from 'react'
import {ArrowLeft, ArrowRight, CircleCheck, CircleHelp, LoaderCircle} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Badge} from '../src/components/ui/badge'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../src/components/ui/table'
import {Checkbox} from '../src/components/ui/checkbox'
import {NativeSelect, NativeSelectOption} from '../src/components/ui/native-select'
import {canStartCheck, modeName, observeCheck, quotaPoints, startCheck, type CheckPlan, type CheckPreparationState, type CoverageRow} from './check-preparation-state'
import './check-preparation.css'
import {readUsage, requestUsage, usageHref, type CheckOwner} from './usage-state'

const layouts = ['覆盖优先', '方案优先', '检查简报']
const rowLabel: Record<CoverageRow['state'], string> = {ready: '有检查计划', investigate: '需调查对应关系', gap: '资料缺口', unsupported: '尚未适配', excluded: '不适用'}
export function CheckPreparation({caseId, state, plan, onChange, onContinue, onHome, saveError, recheck}: {
  caseId: string
  state: CheckPreparationState; plan: CheckPlan; onChange: (state: SetStateAction<CheckPreparationState | undefined>) => void
  onContinue: () => void; onHome: () => void; saveError: string
  recheck?: {owner: CheckOwner; description: string; note: string; backLabel?: string}
}) {
  const [layout, setLayout] = useState(() => Math.max(0, layouts.indexOf(new URLSearchParams(location.search).get('checkLayout') ?? '')))
  const request = state.request
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {window.scrollTo({top: 0}); heading.current?.focus({preventScroll: true})}, [])
  const frozen = request?.plan ?? plan
  const [, setRefresh] = useState(0)
  useEffect(() => {
    const refresh = () => setRefresh(value => value + 1)
    window.addEventListener('storage', refresh); window.addEventListener('focus', refresh)
    return () => {window.removeEventListener('storage', refresh); window.removeEventListener('focus', refresh)}
  }, [])
  const usage = readUsage({caseId, check: state, owner: recheck?.owner})
  const allowance = quotaPoints(state.quota)
  const points = usage.available === null || allowance === null ? null : Math.min(usage.available, allowance)
  const actionable = frozen.rows.filter(row => ['ready', 'investigate'].includes(row.state)).length
  const investigation = frozen.rows.filter(row => row.state === 'investigate').length
  const missing = frozen.rows.filter(row => ['gap', 'unsupported'].includes(row.state)).length
  const update = (patch: Partial<CheckPreparationState>) => onChange(current => ({...state, ...current, ...patch}))
  function chooseLayout(next: number) {setLayout(next); const url = new URL(location.href); url.searchParams.set('checkLayout', layouts[next]); history.replaceState(null, '', url)}
  useEffect(() => {
    if (request?.phase !== 'requesting' || state.outcome === 'waiting') return
    const id = request.id, attempt = request.attempt
    const timer = window.setTimeout(() => onChange(current => current ? observeCheck(current, id, attempt, false) : current), 800)
    return () => window.clearTimeout(timer)
  }, [request?.id, request?.attempt, request?.phase, state.outcome])
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || (event.target as HTMLElement)?.closest('input,textarea,select,button,[role=dialog],[contenteditable=true]')) return
      chooseLayout((layout + (event.key === 'ArrowRight' ? 1 : 2)) % 3)
    }
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key)
  })
  const coverage = <section className="gf-check-coverage" aria-label="本次检查范围"><div className="gf-check-section-head"><h2>本次检查范围</h2><span>{actionable - investigation} 条已有计划{investigation > 0 && ` · ${investigation} 条待调查`}{missing > 0 && ` · ${missing} 条缺口`}</span></div>
    <Table><TableHeader><TableRow><TableHead>适用要求与原件</TableHead><TableHead>计划处理</TableHead></TableRow></TableHeader><TableBody>{frozen.rows.map(row => <TableRow key={row.id}><TableCell><strong>{row.id} · {row.title}</strong><small>{row.sources.join('、') || (row.state === 'excluded' ? '不纳入本次检查' : '对应原件尚未确定')}</small></TableCell><TableCell><Badge variant="outline">{rowLabel[row.state]}</Badge><p>{row.detail}</p></TableCell></TableRow>)}</TableBody></Table>
    <p className="gf-check-fine">这里说明准备如何检查，不是检查结论。缺口会保留到结果中。</p>
  </section>
  const method = <fieldset className="gf-check-method" disabled={Boolean(request)}><legend>检查方式</legend>
    {(['standard', 'deep'] as const).map(mode => <label key={mode} data-selected={frozen.mode === mode}><input type="radio" name="check-mode" value={mode} checked={frozen.mode === mode} onChange={() => update({mode})}/><span><strong>{modeName(mode)} <small>{mode === 'standard' ? 'Standard' : 'Deep'}</small></strong><span>{mode === 'standard' ? '执行已确认适配的规则与模板检查；未适配条款保留未覆盖。' : '调查原件结构、条款含义与跨文件关联；证据不足时仍可能无法判断。'}</span></span></label>)}
  </fieldset>
  const estimate = <section className="gf-check-estimate" aria-label="预计用量"><h2>预计用量 <small>示例估算</small></h2><dl><div><dt>本次预计</dt><dd>{frozen.estimate.low}–{frozen.estimate.high} 点</dd></div><div><dt>预计时间</dt><dd>{frozen.estimate.minutes} 分钟</dd></div><div><dt>账号可用额度</dt><dd>{points === null ? '待核对' : `${points} 点`}</dd></div></dl><Button variant="link" asChild><a href={usageHref(recheck?.owner)}>查看账号用量<ArrowRight size={15}/></a></Button>
    {(!request || request.phase === 'absent') && (points === null || points < frozen.estimate.high) && <div className="gf-check-warning" role="status"><CircleHelp size={18}/><div><strong>{points === null ? '暂时无法确认额度' : '当前额度不足'}</strong><p>{points === null ? '先重新读取额度。未发起检查，选择已保留。' : `此方案需预留 ${frozen.estimate.high} 点。可选择更低用量的适用方案，或待额度补足再开始。`}</p><Button variant="outline" onClick={() => update({quota: 'available'})}>模拟重新读取额度</Button></div></div>}
    {usage.incomplete && <p className="gf-check-fine">账号有缺失或不可读的用量记录。重新读取不会把它们当作零；请到账号用量查看。</p>}<p className="gf-check-fine">点数与时间只用于体验验证，不代表正式价格或完成承诺。</p>
  </section>
  const acceptance = request && <section className="gf-check-acceptance" aria-label="检查受理状态" role="status">
    <div className="gf-check-section-head"><h2>{request.phase === 'accepted' ? '检查已受理' : request.phase === 'absent' ? '检查未受理' : request.phase === 'requesting' ? '正在确认受理' : '受理结果待核对'}</h2>{request.phase === 'accepted' ? <CircleCheck size={20}/> : request.phase === 'requesting' ? <LoaderCircle size={20} className="animate-spin"/> : <CircleHelp size={20}/>}</div>
    <p>{request.phase === 'accepted' ? `检查号 ${request.runId}。现在可以离开，稍后继续。` : request.phase === 'absent' ? '已确认此请求没有开始检查。重试会保留原方案与范围。' : '还不能确认检查是否已经开始。离开页面不会取消请求；先核对，避免重复发起。'}</p>
    <small>{request.id} · 第 {request.attempt} 次连接 · {modeName(request.plan.mode)}</small>
    <p className="gf-check-fine">{requestUsage(request).actual !== null ? `已结算 ${requestUsage(request).actual} 点 · 1 笔；原预留已释放。` : request.phase === 'accepted' ? `本次受理预留 ${request.plan.estimate.high} 点 · 1 笔记录；实际用量待结算。` : request.phase === 'absent' ? '未形成受理用量。' : `受理待核对；继续保留 ${request.plan.estimate.high} 点预留。`}</p>
    {request.queryError && <p className="gf-check-error">暂时无法读取受理状态。原请求仍保留，请稍后再次核对。</p>}
    {['requesting', 'unknown'].includes(request.phase) && <Button className="gf-primary" onClick={() => onChange(current => current ? observeCheck(current, request.id, request.attempt, true) : current)}>核对受理状态</Button>}
    {request.phase === 'accepted' && <Button className="gf-primary" onClick={onContinue}>{recheck ? '查看本次复查' : '查看检查进度'}<ArrowRight size={16}/></Button>}
  </section>
  const actions = <div className="gf-check-actions">{(!request || request.phase === 'absent') && <><Button className="gf-primary" disabled={Boolean(saveError) || !canStartCheck(state, plan, usage.available)} onClick={() => onChange(current => startCheck(current ?? state, plan, readUsage({caseId, check: current ?? state, owner: recheck?.owner}).available))}>{request ? '重试原检查请求' : recheck ? '开始复查' : '开始检查'}<ArrowRight size={16}/></Button>{!actionable && <p>没有可执行的检查计划。可选择深度调查；缺失原件仍须补充。</p>}</>}<Button variant="ghost" onClick={onHome}>{recheck ? recheck.backLabel ?? '返回新旧对照' : '返回工作台'}</Button></div>
  return <div className={`gf-check gf-check--${layout}`}>
    <header className="gf-check-heading"><div><div className="gf-eyebrow">{recheck?.description ?? '资料已登记'}</div><h1 ref={heading} tabIndex={-1}>{request ? '本次检查方案' : recheck ? '复查准备' : '检查准备'}</h1><p>{frozen.basis} · {frozen.files.length} 份原件</p></div>{!recheck && <Badge variant="outline">{frozen.receipt}</Badge>}</header>
    {recheck?.note && <details className="gf-check-docs"><summary>本次变化与来源说明</summary><p className="whitespace-pre-wrap">{recheck.note}</p></details>}
    {acceptance}
    {layout === 2 && <div className="gf-check-brief"><h2>以{modeName(frozen.mode)}处理 {actionable} 条要求</h2><p>{missing ? `${missing} 条缺口会随结果保留。` : '现有要求均有处理计划；是否满足仍待检查与复核。'}</p></div>}
    <div className="gf-check-layout">{layout === 1 ? <><div>{method}{estimate}{actions}</div><div>{coverage}</div></> : <><div>{coverage}</div><div>{method}{estimate}{actions}</div></>}</div>
    <details className="gf-check-docs"><summary>本次采用的原件版本</summary><ul>{frozen.files.map(file => <li key={file}>{file}</li>)}</ul></details>
    {import.meta.env.DEV && <details className="gf-check-simulator"><summary>演示受理与额度情形</summary><div><label>额度<NativeSelect value={state.quota} disabled={request?.phase === 'accepted'} onChange={event => update({quota: event.target.value as CheckPreparationState['quota']})}><NativeSelectOption value="available">2000 点</NativeSelectOption><NativeSelectOption value="low">100 点</NativeSelectOption><NativeSelectOption value="unavailable">读取失败</NativeSelectOption></NativeSelect></label><label>下次受理结果<NativeSelect value={state.outcome} disabled={request !== null && request.phase !== 'absent'} onChange={event => update({outcome: event.target.value as CheckPreparationState['outcome']})}><NativeSelectOption value="accepted">正常受理</NativeSelectOption><NativeSelectOption value="unknown_accepted">已受理，回执丢失</NativeSelectOption><NativeSelectOption value="unknown_absent">未受理，回执丢失</NativeSelectOption><NativeSelectOption value="rejected">明确未受理</NativeSelectOption><NativeSelectOption value="waiting">持续等待，可离开</NativeSelectOption></NativeSelect></label><label className="gf-check-query-failure"><Checkbox checked={state.queryFails} onCheckedChange={value => update({queryFails: value === true})}/>模拟查询失败</label></div>{request?.remote === 'pending' && <div><Button variant="outline" onClick={() => update({request: {...request, remote: 'accepted'}})}>模拟服务已受理</Button><Button variant="outline" onClick={() => update({request: {...request, remote: 'absent'}})}>模拟服务确认未受理</Button></div>}</details>}
    {import.meta.env.DEV && <aside className="gf-switcher" aria-label="检查准备方案切换"><button aria-label="上一种准备布局" onClick={() => chooseLayout((layout + 2) % 3)}><ArrowLeft size={16}/></button><span>布局方案　{layout + 1} / 3 · {layouts[layout]}</span><button aria-label="下一种准备布局" onClick={() => chooseLayout((layout + 1) % 3)}><ArrowRight size={16}/></button></aside>}
    <span className="gf-state-readout">模拟状态：{request?.phase ?? '尚未发起'} · {modeName(frozen.mode)} · {actionable} 条计划</span>
  </div>
}
